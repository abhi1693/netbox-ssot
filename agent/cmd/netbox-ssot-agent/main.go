package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/abhi1693/netbox-ssot/internal/automation"
	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
	providercontract "github.com/abhi1693/netbox-ssot/internal/provider"
	"github.com/abhi1693/netbox-ssot/internal/secrets"
	"github.com/abhi1693/netbox-ssot/internal/submission"
	netboxprovider "github.com/abhi1693/netbox-ssot/providers/netbox"
)

var version = "0.0.1"

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1:], os.Stdin, os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, stdin io.Reader, stdout, stderr io.Writer) error {
	registry, err := providercontract.NewRegistry(netboxprovider.New())
	if err != nil {
		return errors.New("agent collector registry could not be initialized")
	}
	providerCapabilities, err := compiledProviderCapabilities(registry)
	if err != nil {
		return errors.New("agent provider capabilities could not be loaded")
	}
	if len(args) == 0 {
		return errors.New("usage: netbox-ssot-agent <providers|test-connection|collect|generate-key|enroll|rotate-key|submit|config|sync|run|version>")
	}

	switch args[0] {
	case "providers":
		manifests, err := registry.Manifests()
		if err != nil {
			return errors.New("provider manifests could not be loaded")
		}
		return encode(stdout, manifests)
	case "test-connection":
		var request contracts.ConnectionTestRequest
		if err := decodeRequest(args[1:], stdin, &request); err != nil {
			return err
		}
		collector, found := registry.Get(request.ProviderID)
		if !found {
			return errors.New("requested provider is not compiled into this agent")
		}
		return encode(stdout, collector.TestConnection(ctx, request, secrets.LocalResolver{}))
	case "collect":
		var request contracts.CollectionRequest
		if err := decodeRequest(args[1:], stdin, &request); err != nil {
			return err
		}
		collector, found := registry.Get(request.ProviderID)
		if !found {
			return errors.New("requested provider is not compiled into this agent")
		}
		return encode(stdout, collector.Collect(ctx, request, secrets.LocalResolver{}))
	case "generate-key":
		keyPair, err := submission.GenerateKeyPair()
		if err != nil {
			return errors.New("agent signing key could not be generated")
		}
		return encode(stdout, keyPair)
	case "enroll":
		return enrollAgent(ctx, args[1:], stdout, providerCapabilities)
	case "rotate-key":
		return rotateAgentKey(ctx, args[1:], stdout)
	case "submit":
		options, batch, err := decodeSubmission(args[1:])
		if err != nil {
			return err
		}
		privateKey, err := (secrets.LocalResolver{}).Resolve(ctx, options.privateKeyRef)
		if err != nil {
			return encode(stdout, contracts.SubmissionResult{
				Succeeded: false,
				Summary:   "Agent signing key reference could not be resolved.",
				Details:   []contracts.CollectionMessage{{Code: "secret_unavailable", Message: "Agent signing key reference could not be resolved."}},
			})
		}
		result := submission.Submit(ctx, submission.Options{
			Endpoint:          options.endpoint,
			AgentID:           options.agentID,
			PrivateKey:        privateKey,
			VerifyTLS:         options.verifyTLS,
			AllowInsecureHTTP: options.allowInsecureHTTP,
			Timeout:           options.timeout,
		}, batch)
		return encode(stdout, result)
	case "config", "sync", "run":
		options, err := decodeAgentControl(args[0], args[1:])
		if err != nil {
			return err
		}
		logger := slog.New(slog.NewTextHandler(stderr, &slog.HandlerOptions{Level: options.logLevel}))
		privateKey, err := (secrets.LocalResolver{}).Resolve(ctx, options.privateKeyRef)
		if err != nil {
			return errors.New("agent signing key reference could not be resolved")
		}
		runner := automation.Runner{
			Registry:       registry,
			SecretResolver: secrets.LocalResolver{},
			ControlOptions: submission.Options{
				Endpoint:          options.endpoint,
				AgentID:           options.agentID,
				PrivateKey:        privateKey,
				VerifyTLS:         options.verifyTLS,
				AllowInsecureHTTP: options.allowInsecureHTTP,
				Timeout:           options.timeout,
			},
			AgentVersion:         version,
			ControlInterval:      options.pollInterval,
			ProviderCapabilities: providerCapabilities,
			Logger:               logger,
		}
		if args[0] == "config" {
			configuration, err := runner.Fetch(ctx)
			if err != nil {
				return err
			}
			return encode(stdout, configuration)
		}
		if args[0] == "sync" {
			logger.InfoContext(ctx, "one-time synchronization started", "agent_id", options.agentID)
			result := runner.Sync(ctx)
			if result.Succeeded {
				logger.InfoContext(ctx, "one-time synchronization completed",
					"assignments", result.AssignmentCount,
					"succeeded", result.CompletedCount,
					"failed", result.FailedCount,
				)
			} else {
				logger.WarnContext(ctx, "one-time synchronization completed with failures",
					"assignments", result.AssignmentCount,
					"succeeded", result.CompletedCount,
					"failed", result.FailedCount,
				)
			}
			return encode(stdout, result)
		}
		return runDaemon(ctx, stdout, runner, options.agentID, options.endpoint, options.pollInterval, logger)
	case "version":
		_, err := fmt.Fprintln(stdout, version)
		return err
	default:
		return errors.New("unknown command")
	}
}

func compiledProviderCapabilities(
	registry *providercontract.Registry,
) ([]contracts.AgentProviderCapability, error) {
	manifests, err := registry.Manifests()
	if err != nil {
		return nil, err
	}
	capabilities := make([]contracts.AgentProviderCapability, 0, len(manifests))
	for _, manifest := range manifests {
		capabilities = append(capabilities, contracts.AgentProviderCapability{
			ProviderID: manifest.ProviderID, ImplementationVersion: manifest.ImplementationVersion,
			ContractVersion: manifest.ContractVersion,
		})
	}
	return capabilities, nil
}

func defaultPrivateKeyReference() string {
	if reference := os.Getenv("NETBOX_SSOT_PRIVATE_KEY_REF"); reference != "" {
		return reference
	}
	return "env://NETBOX_SSOT_SIGNING_KEY"
}

type enrollmentOptions struct {
	endpoint          string
	tokenRef          string
	privateKeyPath    string
	verifyTLS         bool
	allowInsecureHTTP bool
	timeout           time.Duration
}

func enrollAgent(
	ctx context.Context,
	args []string,
	stdout io.Writer,
	providerCapabilities []contracts.AgentProviderCapability,
) error {
	options, err := decodeEnrollment(args)
	if err != nil {
		return err
	}
	token, err := (secrets.LocalResolver{}).Resolve(ctx, options.tokenRef)
	if err != nil {
		return errors.New("agent enrollment token reference could not be resolved")
	}
	keyPair, err := submission.GenerateKeyPair()
	if err != nil {
		return errors.New("agent signing key could not be generated")
	}
	if err := writeNewPrivateKey(options.privateKeyPath, keyPair.PrivateKey); err != nil {
		return err
	}
	removeKey := true
	defer func() {
		if removeKey {
			_ = os.Remove(options.privateKeyPath)
		}
	}()
	response, err := submission.Enroll(ctx, submission.Options{
		Endpoint: options.endpoint, VerifyTLS: options.verifyTLS,
		AllowInsecureHTTP: options.allowInsecureHTTP, Timeout: options.timeout,
	}, contracts.AgentEnrollmentRequest{
		Token: token, PublicKey: keyPair.PublicKey, AgentVersion: version,
		ProtocolVersion: contracts.AgentProtocolVersion, Providers: providerCapabilities,
	}, version)
	if err != nil {
		return err
	}
	removeKey = false
	return encode(stdout, response)
}

func decodeEnrollment(args []string) (enrollmentOptions, error) {
	flags := flag.NewFlagSet("enroll", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	endpoint := flags.String("endpoint", os.Getenv("NETBOX_SSOT_ENROLLMENT_ENDPOINT"), "NetBox SSoT enrollment endpoint")
	tokenRef := flags.String("token-ref", "env://NETBOX_SSOT_ENROLLMENT_TOKEN", "one-time enrollment token reference")
	privateKeyPath := flags.String("private-key-path", "/etc/netbox-ssot-agent/signing-key", "new private key path")
	verifyTLS := flags.Bool("verify-tls", true, "verify NetBox TLS certificates")
	allowInsecureHTTP := flags.Bool("allow-insecure-http", false, "allow HTTP for local development")
	timeout := flags.Duration("timeout", 30*time.Second, "NetBox request timeout")
	if err := flags.Parse(args); err != nil || *endpoint == "" || !filepath.IsAbs(*privateKeyPath) ||
		*timeout < time.Second || *timeout > 2*time.Minute {
		return enrollmentOptions{}, errors.New("invalid agent enrollment options")
	}
	return enrollmentOptions{
		endpoint: *endpoint, tokenRef: *tokenRef, privateKeyPath: filepath.Clean(*privateKeyPath),
		verifyTLS: *verifyTLS, allowInsecureHTTP: *allowInsecureHTTP, timeout: *timeout,
	}, nil
}

func writeNewPrivateKey(path string, value string) error {
	directory := filepath.Dir(path)
	directoryInfo, err := os.Stat(directory)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("private key directory %s does not exist; create it with mode 0700", directory)
		}
		if os.IsPermission(err) {
			return fmt.Errorf("private key directory %s cannot be accessed: permission denied", directory)
		}
		return fmt.Errorf("private key directory %s could not be inspected: %w", directory, err)
	}
	if !directoryInfo.IsDir() {
		return fmt.Errorf("private key directory path %s is not a directory", directory)
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		if os.IsExist(err) {
			return errors.New("private key file could not be created; the target already exists")
		}
		if os.IsPermission(err) {
			return fmt.Errorf("private key file could not be created in %s: permission denied", directory)
		}
		return fmt.Errorf("private key file could not be created: %w", err)
	}
	failed := true
	defer func() {
		_ = file.Close()
		if failed {
			_ = os.Remove(path)
		}
	}()
	if _, err := io.WriteString(file, value+"\n"); err != nil || file.Sync() != nil || file.Close() != nil {
		return errors.New("private key file could not be written")
	}
	failed = false
	return nil
}

type rotationOptions struct {
	endpoint          string
	agentID           string
	privateKeyRef     string
	verifyTLS         bool
	allowInsecureHTTP bool
	timeout           time.Duration
}

func rotateAgentKey(ctx context.Context, args []string, stdout io.Writer) error {
	options, err := decodeRotation(args)
	if err != nil {
		return err
	}
	privateKeyPath, err := secrets.FileReferencePath(options.privateKeyRef)
	if err != nil {
		return errors.New("key rotation requires an absolute file:// private-key reference")
	}
	currentKey, err := (secrets.LocalResolver{}).Resolve(ctx, options.privateKeyRef)
	if err != nil {
		return errors.New("agent signing key reference could not be resolved")
	}
	keyPair, err := submission.GenerateKeyPair()
	if err != nil {
		return errors.New("replacement signing key could not be generated")
	}
	temporary, err := os.CreateTemp(filepath.Dir(privateKeyPath), ".netbox-ssot-signing-key-")
	if err != nil {
		return errors.New("replacement signing key file could not be created")
	}
	temporaryPath := temporary.Name()
	cleanup := true
	defer func() {
		_ = temporary.Close()
		if cleanup {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return errors.New("replacement signing key file permissions could not be secured")
	}
	if _, err := io.WriteString(temporary, keyPair.PrivateKey+"\n"); err != nil ||
		temporary.Sync() != nil || temporary.Close() != nil {
		return errors.New("replacement signing key file could not be written")
	}
	response, err := submission.RotateKey(ctx, submission.Options{
		Endpoint: options.endpoint, AgentID: options.agentID, PrivateKey: currentKey,
		VerifyTLS: options.verifyTLS, AllowInsecureHTTP: options.allowInsecureHTTP, Timeout: options.timeout,
	}, contracts.AgentKeyRotationRequest{PublicKey: keyPair.PublicKey, AgentVersion: version}, version)
	if err != nil {
		return err
	}
	if err := os.Rename(temporaryPath, privateKeyPath); err != nil {
		return errors.New("NetBox accepted the new key, but the local private key could not be replaced; retry within the rotation grace period")
	}
	cleanup = false
	return encode(stdout, response)
}

func decodeRotation(args []string) (rotationOptions, error) {
	flags := flag.NewFlagSet("rotate-key", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	endpoint := flags.String("endpoint", os.Getenv("NETBOX_SSOT_KEY_ROTATION_ENDPOINT"), "NetBox SSoT key rotation endpoint")
	agentID := flags.String("agent-id", os.Getenv("NETBOX_SSOT_AGENT_ID"), "registered collector agent UUID")
	privateKeyRef := flags.String("private-key-ref", defaultPrivateKeyReference(), "file-backed agent signing key reference")
	verifyTLS := flags.Bool("verify-tls", true, "verify NetBox TLS certificates")
	allowInsecureHTTP := flags.Bool("allow-insecure-http", false, "allow HTTP for local development")
	timeout := flags.Duration("timeout", 30*time.Second, "NetBox request timeout")
	if err := flags.Parse(args); err != nil || *endpoint == "" || *agentID == "" ||
		*timeout < time.Second || *timeout > 2*time.Minute {
		return rotationOptions{}, errors.New("invalid agent key rotation options")
	}
	return rotationOptions{
		endpoint: *endpoint, agentID: *agentID, privateKeyRef: *privateKeyRef,
		verifyTLS: *verifyTLS, allowInsecureHTTP: *allowInsecureHTTP, timeout: *timeout,
	}, nil
}

type agentControlOptions struct {
	endpoint          string
	agentID           string
	privateKeyRef     string
	verifyTLS         bool
	allowInsecureHTTP bool
	timeout           time.Duration
	pollInterval      time.Duration
	logLevel          slog.Level
}

func decodeAgentControl(command string, args []string) (agentControlOptions, error) {
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	endpoint := flags.String("endpoint", os.Getenv("NETBOX_SSOT_CONTROL_ENDPOINT"), "NetBox SSoT agent configuration endpoint")
	agentID := flags.String("agent-id", os.Getenv("NETBOX_SSOT_AGENT_ID"), "registered collector agent UUID")
	privateKeyRef := flags.String("private-key-ref", defaultPrivateKeyReference(), "agent signing key secret reference")
	verifyTLS := flags.Bool("verify-tls", true, "verify NetBox TLS certificates")
	allowInsecureHTTP := flags.Bool("allow-insecure-http", false, "allow HTTP for local development")
	timeout := flags.Duration("timeout", 30*time.Second, "NetBox request timeout")
	pollInterval := flags.Duration(
		"poll-interval",
		5*time.Second,
		"control and admin-action polling interval (2s to 30s; independent of collection schedules)",
	)
	defaultLogLevel := os.Getenv("NETBOX_SSOT_LOG_LEVEL")
	if defaultLogLevel == "" {
		defaultLogLevel = "info"
	}
	logLevelValue := flags.String("log-level", defaultLogLevel, "log level: debug, info, warn, or error")
	if err := flags.Parse(args); err != nil || *endpoint == "" || *agentID == "" {
		return agentControlOptions{}, errors.New("invalid agent control options")
	}
	if *timeout < time.Second || *timeout > 2*time.Minute || *pollInterval < 2*time.Second || *pollInterval > 30*time.Second {
		return agentControlOptions{}, errors.New("invalid agent control timing options")
	}
	logLevel, err := parseLogLevel(*logLevelValue)
	if err != nil {
		return agentControlOptions{}, err
	}
	return agentControlOptions{
		endpoint:          *endpoint,
		agentID:           *agentID,
		privateKeyRef:     *privateKeyRef,
		verifyTLS:         *verifyTLS,
		allowInsecureHTTP: *allowInsecureHTTP,
		timeout:           *timeout,
		pollInterval:      *pollInterval,
		logLevel:          logLevel,
	}, nil
}

func parseLogLevel(value string) (slog.Level, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "debug":
		return slog.LevelDebug, nil
	case "info":
		return slog.LevelInfo, nil
	case "warn", "warning":
		return slog.LevelWarn, nil
	case "error":
		return slog.LevelError, nil
	default:
		return slog.LevelInfo, errors.New("invalid agent log level; expected debug, info, warn, or error")
	}
}

type sourceSchedule struct {
	revision string
	nextRun  time.Time
}

type configurationLogState struct {
	initialized     bool
	agentName       string
	pollInterval    time.Duration
	sourceRevisions map[string]string
}

const (
	maxConcurrentAgentWork = 4
	maxAgentRetryBackoff   = 5 * time.Minute
)

func nextAgentRetryDelay(baseInterval time.Duration, previousDelay time.Duration) time.Duration {
	if baseInterval <= 0 {
		baseInterval = 5 * time.Second
	}
	if baseInterval >= maxAgentRetryBackoff {
		return maxAgentRetryBackoff
	}
	if previousDelay < baseInterval {
		return baseInterval
	}
	if previousDelay >= maxAgentRetryBackoff/2 {
		return maxAgentRetryBackoff
	}
	return previousDelay * 2
}

type daemonFetchResult struct {
	configuration contracts.AgentConfigurationResponse
	err           error
}

type sourceWorkResult struct {
	assignment contracts.AgentAssignment
	result     contracts.SourceSyncResult
}

type commandWorkResult struct {
	command contracts.AgentCommand
	result  contracts.AgentCommandResult
	started time.Time
}

type commandReportResult struct {
	commandID string
	result    contracts.AgentCommandResult
	err       error
}

func runDaemon(
	ctx context.Context,
	stdout io.Writer,
	runner automation.Runner,
	agentID string,
	controlEndpoint string,
	pollInterval time.Duration,
	logger *slog.Logger,
) error {
	logger.InfoContext(ctx, "agent started",
		"agent_id", agentID,
		"version", version,
		"providers", formatProviderCapabilities(runner.ProviderCapabilities),
		"control_endpoint", controlEndpoint,
		"poll_interval", pollInterval,
	)
	schedules := make(map[string]sourceSchedule)
	runningSources := make(map[string]bool)
	collectingSources := make(map[string]bool)
	knownCommands := make(map[string]bool)
	pendingReports := make(map[string]contracts.AgentCommandResult)
	reportingCommands := make(map[string]bool)
	completedCommands := make(map[string]time.Time)
	queuedCommands := make([]contracts.AgentCommand, 0)
	assignments := make([]contracts.AgentAssignment, 0)
	configurationState := configurationLogState{}
	ingestEndpoint := ""
	commandResultEndpoint := ""
	commandStatusEndpoint := ""
	activeWorkers := 0
	fetching := false
	controlRetryDelay := time.Duration(0)
	consecutiveControlFailures := 0

	fetchResults := make(chan daemonFetchResult, 1)
	sourceResults := make(chan sourceWorkResult, maxConcurrentAgentWork)
	commandResults := make(chan commandWorkResult, maxConcurrentAgentWork)
	reportResults := make(chan commandReportResult, 100)
	pollTimer := time.NewTimer(0)
	defer pollTimer.Stop()

	startPendingReports := func() {
		if commandResultEndpoint == "" {
			return
		}
		for commandID, result := range pendingReports {
			if reportingCommands[commandID] {
				continue
			}
			reportingCommands[commandID] = true
			go func(commandID string, result contracts.AgentCommandResult, endpoint string) {
				err := runner.ReportCommandResult(ctx, endpoint, result)
				reportResults <- commandReportResult{commandID: commandID, result: result, err: err}
			}(commandID, result, commandResultEndpoint)
		}
	}

	dispatchWork := func() {
		for activeWorkers < maxConcurrentAgentWork {
			commandIndex := -1
			for index, command := range queuedCommands {
				if !runningSources[command.Assignment.SourceID] {
					commandIndex = index
					break
				}
			}
			if commandIndex >= 0 {
				command := queuedCommands[commandIndex]
				queuedCommands = append(queuedCommands[:commandIndex], queuedCommands[commandIndex+1:]...)
				runningSources[command.Assignment.SourceID] = true
				if command.Kind == "run_now" {
					collectingSources[command.Assignment.SourceID] = true
				}
				activeWorkers++
				logger.InfoContext(ctx, "command execution started",
					"command_id", command.CommandID,
					"source_id", command.Assignment.SourceID,
					"source_name", command.Assignment.SourceName,
					"kind", command.Kind,
					"active_workers", activeWorkers,
				)
				go func(command contracts.AgentCommand, statusEndpoint string, ingestEndpoint string) {
					started := time.Now()
					runningUpdate := contracts.AgentCommandStatusUpdate{
						CommandID: command.CommandID, SourceID: command.Assignment.SourceID, Kind: command.Kind,
						State: "running", Summary: "Agent started executing the command.", OccurredAt: time.Now().UTC(),
					}
					if err := runner.ReportCommandStatus(ctx, statusEndpoint, runningUpdate); err != nil {
						logger.WarnContext(ctx, "command running status report failed",
							"command_id", command.CommandID, "error", err)
					}
					result := runner.ExecuteCommand(ctx, ingestEndpoint, command)
					reportingUpdate := contracts.AgentCommandStatusUpdate{
						CommandID: command.CommandID, SourceID: command.Assignment.SourceID, Kind: command.Kind,
						State: "reporting", Summary: "Execution finished; delivering the result to NetBox.",
						OccurredAt: time.Now().UTC(),
					}
					if err := runner.ReportCommandStatus(ctx, statusEndpoint, reportingUpdate); err != nil {
						logger.WarnContext(ctx, "command reporting status report failed",
							"command_id", command.CommandID, "error", err)
					}
					commandResults <- commandWorkResult{command: command, result: result, started: started}
				}(command, commandStatusEndpoint, ingestEndpoint)
				continue
			}

			now := time.Now()
			startedSource := false
			for _, assignment := range assignments {
				if !scheduledCollectionEnabled(assignment) {
					continue
				}
				if runningSources[assignment.SourceID] {
					continue
				}
				schedule, found := schedules[assignment.SourceID]
				if found && schedule.revision == assignment.Revision && now.Before(schedule.nextRun) {
					continue
				}
				reason := "scheduled"
				if !found {
					reason = "new_assignment"
				} else if schedule.revision != assignment.Revision {
					reason = "configuration_changed"
				}
				runningSources[assignment.SourceID] = true
				collectingSources[assignment.SourceID] = true
				activeWorkers++
				startedSource = true
				logger.InfoContext(ctx, "scheduled source work dispatched",
					"source_id", assignment.SourceID,
					"source_name", assignment.SourceName,
					"reason", reason,
					"active_workers", activeWorkers,
				)
				go func(assignment contracts.AgentAssignment, endpoint string) {
					sourceResults <- sourceWorkResult{
						assignment: assignment,
						result:     runner.Execute(ctx, endpoint, assignment),
					}
				}(assignment, ingestEndpoint)
				break
			}
			if !startedSource {
				break
			}
		}
	}

	for {
		select {
		case <-ctx.Done():
			logger.InfoContext(context.WithoutCancel(ctx), "agent stopped",
				"active_workers", activeWorkers,
				"queued_commands", len(queuedCommands),
				"pending_reports", len(pendingReports),
			)
			return nil
		case <-pollTimer.C:
			if fetching {
				pollTimer.Reset(pollInterval)
				continue
			}
			fetching = true
			activeCommandIDs := make([]string, 0, len(knownCommands))
			for commandID := range knownCommands {
				activeCommandIDs = append(activeCommandIDs, commandID)
			}
			sort.Strings(activeCommandIDs)
			activeSourceIDs := make([]string, 0, len(collectingSources))
			for sourceID := range collectingSources {
				activeSourceIDs = append(activeSourceIDs, sourceID)
			}
			sort.Strings(activeSourceIDs)
			logger.DebugContext(ctx, "fetching agent configuration",
				"active_commands", len(activeCommandIDs),
				"active_sources", len(activeSourceIDs),
				"active_workers", activeWorkers,
			)
			fetchRunner := runner
			go func() {
				configuration, err := fetchRunner.FetchWithActivity(ctx, activeCommandIDs, activeSourceIDs)
				fetchResults <- daemonFetchResult{configuration: configuration, err: err}
			}()
		case fetched := <-fetchResults:
			fetching = false
			if fetched.err != nil {
				controlRetryDelay = nextAgentRetryDelay(pollInterval, controlRetryDelay)
				consecutiveControlFailures++
				logger.WarnContext(ctx, "agent configuration fetch failed",
					"error", fetched.err,
					"consecutive_failures", consecutiveControlFailures,
					"retry_in", controlRetryDelay,
				)
				if encodeErr := encode(stdout, contracts.AgentSyncResult{
					Succeeded: false,
					Summary:   "Agent configuration could not be fetched; retrying.",
					Results:   []contracts.SourceSyncResult{},
					Details: []contracts.CollectionMessage{{
						Code: "control_unavailable", Message: fetched.err.Error(), Retryable: true,
					}},
				}); encodeErr != nil {
					return encodeErr
				}
				pollTimer.Reset(controlRetryDelay)
				continue
			}
			if consecutiveControlFailures > 0 {
				logger.InfoContext(ctx, "agent control connection recovered",
					"consecutive_failures", consecutiveControlFailures,
				)
			}
			controlRetryDelay = 0
			consecutiveControlFailures = 0
			configuration := fetched.configuration
			if configuredInterval := configuration.ControlIntervalSeconds; configuredInterval != 0 {
				nextPollInterval := time.Duration(configuredInterval) * time.Second
				if nextPollInterval != pollInterval {
					logger.InfoContext(ctx, "control polling interval updated by NetBox",
						"previous_interval", pollInterval,
						"poll_interval", nextPollInterval,
					)
					pollInterval = nextPollInterval
					runner.ControlInterval = nextPollInterval
				}
			}
			ingestEndpoint = configuration.IngestEndpoint
			commandResultEndpoint = configuration.CommandResultEndpoint
			commandStatusEndpoint = configuration.CommandStatusEndpoint
			assignments = append(assignments[:0], configuration.Assignments...)
			configurationState = logConfigurationChanges(
				ctx,
				logger,
				configurationState,
				configuration,
				pollInterval,
			)
			logger.DebugContext(ctx, "agent configuration fetched",
				"assignments", len(configuration.Assignments),
				"commands", len(configuration.Commands),
				"generated_at", configuration.GeneratedAt,
			)
			now := time.Now()
			for commandID, completedAt := range completedCommands {
				if now.Sub(completedAt) > 10*time.Minute {
					delete(completedCommands, commandID)
				}
			}
			for _, command := range configuration.Commands {
				if knownCommands[command.CommandID] {
					continue
				}
				if _, completed := completedCommands[command.CommandID]; completed {
					continue
				}
				knownCommands[command.CommandID] = true
				queuedCommands = append(queuedCommands, command)
				logger.InfoContext(ctx, "command queued",
					"command_id", command.CommandID,
					"source_id", command.Assignment.SourceID,
					"source_name", command.Assignment.SourceName,
					"kind", command.Kind,
					"queue_depth", len(queuedCommands),
				)
			}
			active := make(map[string]bool, len(configuration.Assignments))
			for _, assignment := range configuration.Assignments {
				active[assignment.SourceID] = true
			}
			for sourceID := range schedules {
				if !active[sourceID] && !runningSources[sourceID] {
					delete(schedules, sourceID)
				}
			}
			startPendingReports()
			dispatchWork()
			pollTimer.Reset(pollInterval)
		case completed := <-sourceResults:
			activeWorkers--
			delete(runningSources, completed.assignment.SourceID)
			delete(collectingSources, completed.assignment.SourceID)
			if err := encode(stdout, completed.result); err != nil {
				return err
			}
			delay := time.Duration(completed.assignment.IntervalSeconds) * time.Second
			if !completed.result.Succeeded && hasRetryableDetail(completed.result.Details) && delay > time.Minute {
				delay = time.Minute
			}
			schedules[completed.assignment.SourceID] = sourceSchedule{
				revision: completed.assignment.Revision,
				nextRun:  time.Now().Add(delay),
			}
			logger.InfoContext(ctx, "scheduled source work completed",
				"source_id", completed.assignment.SourceID,
				"source_name", completed.assignment.SourceName,
				"succeeded", completed.result.Succeeded,
				"next_run", schedules[completed.assignment.SourceID].nextRun,
				"active_workers", activeWorkers,
			)
			dispatchWork()
		case completed := <-commandResults:
			activeWorkers--
			delete(runningSources, completed.command.Assignment.SourceID)
			if completed.command.Kind == "run_now" {
				delete(collectingSources, completed.command.Assignment.SourceID)
			}
			completed.result.DurationSeconds = time.Since(completed.started).Seconds()
			pendingReports[completed.command.CommandID] = completed.result
			if completed.command.Kind == "run_now" {
				schedules[completed.command.Assignment.SourceID] = sourceSchedule{
					revision: completed.command.Assignment.Revision,
					nextRun: time.Now().Add(
						time.Duration(completed.command.Assignment.IntervalSeconds) * time.Second,
					),
				}
			}
			if err := encode(stdout, completed.result); err != nil {
				return err
			}
			logger.InfoContext(ctx, "command execution finished",
				"command_id", completed.command.CommandID,
				"source_id", completed.result.SourceID,
				"kind", completed.result.Kind,
				"succeeded", completed.result.Succeeded,
				"summary", completed.result.Summary,
				"duration", time.Since(completed.started),
				"active_workers", activeWorkers,
			)
			startPendingReports()
			dispatchWork()
		case reported := <-reportResults:
			delete(reportingCommands, reported.commandID)
			if reported.err != nil {
				logger.WarnContext(ctx, "command result report failed; retrying after next check-in",
					"command_id", reported.commandID,
					"source_id", reported.result.SourceID,
					"kind", reported.result.Kind,
					"error", reported.err,
				)
				continue
			}
			delete(pendingReports, reported.commandID)
			delete(knownCommands, reported.commandID)
			completedCommands[reported.commandID] = time.Now()
			logger.InfoContext(ctx, "command result reported",
				"command_id", reported.commandID,
				"source_id", reported.result.SourceID,
				"kind", reported.result.Kind,
				"succeeded", reported.result.Succeeded,
			)
		}
	}
}

func logConfigurationChanges(
	ctx context.Context,
	logger *slog.Logger,
	previous configurationLogState,
	configuration contracts.AgentConfigurationResponse,
	pollInterval time.Duration,
) configurationLogState {
	current := configurationLogState{
		initialized:     true,
		agentName:       configuration.AgentName,
		pollInterval:    pollInterval,
		sourceRevisions: make(map[string]string, len(configuration.Assignments)),
	}
	changed := !previous.initialized || previous.agentName != current.agentName || previous.pollInterval != pollInterval
	for _, assignment := range configuration.Assignments {
		scheduleEnabled := scheduledCollectionEnabled(assignment)
		sourceState := fmt.Sprintf("%s|%t|%s", assignment.Revision, scheduleEnabled, assignment.SchedulePauseReason)
		current.sourceRevisions[assignment.SourceID] = sourceState
		if previous.sourceRevisions[assignment.SourceID] != sourceState {
			changed = true
			change := "added"
			if _, found := previous.sourceRevisions[assignment.SourceID]; found {
				change = "updated"
			}
			logger.InfoContext(ctx, "source configuration received",
				"change", change,
				"source_id", assignment.SourceID,
				"source_name", assignment.SourceName,
				"provider_id", assignment.ProviderID,
				"datasets", strings.Join(assignment.Datasets, ","),
				"collection_interval", time.Duration(assignment.IntervalSeconds)*time.Second,
				"revision", assignment.Revision,
				"schedule_enabled", scheduleEnabled,
				"schedule_pause_reason", assignment.SchedulePauseReason,
			)
		}
	}
	for sourceID := range previous.sourceRevisions {
		if _, found := current.sourceRevisions[sourceID]; !found {
			changed = true
			logger.InfoContext(ctx, "source configuration removed", "source_id", sourceID)
		}
	}
	if changed {
		message := "agent configuration updated"
		if !previous.initialized {
			message = "agent configuration received"
		}
		logger.InfoContext(ctx, message,
			"agent_id", configuration.AgentID,
			"agent_name", configuration.AgentName,
			"generated_at", configuration.GeneratedAt,
			"assignments", len(configuration.Assignments),
			"control_poll_interval", pollInterval,
		)
	}
	return current
}

func scheduledCollectionEnabled(assignment contracts.AgentAssignment) bool {
	return assignment.ScheduleEnabled == nil || *assignment.ScheduleEnabled
}

func formatProviderCapabilities(capabilities []contracts.AgentProviderCapability) string {
	providers := make([]string, 0, len(capabilities))
	for _, capability := range capabilities {
		providers = append(
			providers,
			capability.ProviderID+"@"+capability.ImplementationVersion+"/"+capability.ContractVersion,
		)
	}
	sort.Strings(providers)
	return strings.Join(providers, ",")
}

func hasRetryableDetail(details []contracts.CollectionMessage) bool {
	for _, detail := range details {
		if detail.Retryable {
			return true
		}
	}
	return false
}

type submissionOptions struct {
	endpoint          string
	agentID           string
	privateKeyRef     string
	verifyTLS         bool
	allowInsecureHTTP bool
	timeout           time.Duration
}

func decodeSubmission(args []string) (submissionOptions, []byte, error) {
	flags := flag.NewFlagSet("submit", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	batchPath := flags.String("batch", "", "observation batch JSON file")
	endpoint := flags.String("endpoint", "", "NetBox SSoT batch ingest endpoint")
	agentID := flags.String("agent-id", "", "registered collector agent UUID")
	privateKeyRef := flags.String("private-key-ref", defaultPrivateKeyReference(), "agent signing key secret reference")
	verifyTLS := flags.Bool("verify-tls", true, "verify the ingest endpoint TLS certificate")
	allowInsecureHTTP := flags.Bool("allow-insecure-http", false, "allow HTTP for local development")
	timeout := flags.Duration("timeout", 30*time.Second, "submission timeout")
	if err := flags.Parse(args); err != nil || *batchPath == "" || *endpoint == "" || *agentID == "" {
		return submissionOptions{}, nil, errors.New("invalid submit command options")
	}
	file, err := os.Open(*batchPath)
	if err != nil {
		return submissionOptions{}, nil, errors.New("batch file could not be opened")
	}
	defer file.Close()
	batch, err := io.ReadAll(io.LimitReader(file, (64<<20)+1))
	if err != nil {
		return submissionOptions{}, nil, errors.New("batch file could not be read")
	}
	return submissionOptions{
		endpoint:          *endpoint,
		agentID:           *agentID,
		privateKeyRef:     *privateKeyRef,
		verifyTLS:         *verifyTLS,
		allowInsecureHTTP: *allowInsecureHTTP,
		timeout:           *timeout,
	}, batch, nil
}

func decodeRequest(args []string, stdin io.Reader, target any) error {
	flags := flag.NewFlagSet("request", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	requestPath := flags.String("request", "-", "JSON request file, or - for stdin")
	if err := flags.Parse(args); err != nil {
		return errors.New("invalid command options")
	}
	reader := stdin
	if *requestPath != "-" {
		file, err := os.Open(*requestPath)
		if err != nil {
			return errors.New("request file could not be opened")
		}
		defer file.Close()
		reader = file
	}
	decoder := json.NewDecoder(io.LimitReader(reader, 4<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return errors.New("request is not valid contract JSON")
	}
	return nil
}

func encode(writer io.Writer, value any) error {
	encoder := json.NewEncoder(writer)
	encoder.SetEscapeHTML(true)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(value); err != nil {
		return errors.New("agent response could not be encoded")
	}
	return nil
}
