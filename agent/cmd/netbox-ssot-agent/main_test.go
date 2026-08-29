package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/abhi1693/netbox-ssot/internal/automation"
	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
	providercontract "github.com/abhi1693/netbox-ssot/internal/provider"
	"github.com/abhi1693/netbox-ssot/internal/submission"
)

type blockingCollector struct {
	started chan struct{}
	release chan struct{}
}

func (c blockingCollector) Manifest() (contracts.ProviderManifest, error) {
	return contracts.ProviderManifest{
		ProviderID: "blocking", ContractVersion: contracts.ContractVersion,
		AgentCompatibility: contracts.AgentCompatibility{CollectorID: "blocking"},
	}, nil
}

func (blockingCollector) TestConnection(
	context.Context,
	contracts.ConnectionTestRequest,
	providercontract.SecretResolver,
) contracts.ConnectionTestResult {
	return contracts.ConnectionTestResult{Succeeded: true, Summary: "Connected."}
}

func (c blockingCollector) Collect(
	ctx context.Context,
	request contracts.CollectionRequest,
	_ providercontract.SecretResolver,
) contracts.ObservationBatch {
	select {
	case c.started <- struct{}{}:
	default:
	}
	select {
	case <-c.release:
	case <-ctx.Done():
	}
	now := time.Now().UTC()
	return contracts.ObservationBatch{
		RunID: request.RunID, SourceID: request.SourceID, ProviderID: request.ProviderID,
		ProviderVersion: "test", ContractVersion: contracts.ContractVersion, State: "complete",
		StartedAt: now, CompletedAt: now, Datasets: request.Datasets,
		Observations: []contracts.Observation{}, Messages: []contracts.CollectionMessage{},
	}
}

type noOpResolver struct{}

func (noOpResolver) Resolve(context.Context, string) (string, error) { return "unused", nil }

func TestHasRetryableDetail(t *testing.T) {
	if hasRetryableDetail([]contracts.CollectionMessage{{Retryable: false}}) {
		t.Fatal("non-retryable detail was treated as retryable")
	}
	if !hasRetryableDetail([]contracts.CollectionMessage{{Retryable: false}, {Retryable: true}}) {
		t.Fatal("retryable detail was ignored")
	}
}

func TestParseLogLevel(t *testing.T) {
	tests := map[string]slog.Level{
		"debug":   slog.LevelDebug,
		"INFO":    slog.LevelInfo,
		"warning": slog.LevelWarn,
		" error ": slog.LevelError,
	}
	for value, want := range tests {
		got, err := parseLogLevel(value)
		if err != nil {
			t.Fatalf("parseLogLevel(%q) error = %v", value, err)
		}
		if got != want {
			t.Fatalf("parseLogLevel(%q) = %v, want %v", value, got, want)
		}
	}
	if _, err := parseLogLevel("trace"); err == nil {
		t.Fatal("parseLogLevel() accepted an unsupported level")
	}
}

func TestNextAgentRetryDelayUsesCappedExponentialBackoff(t *testing.T) {
	tests := []struct {
		name     string
		base     time.Duration
		previous time.Duration
		want     time.Duration
	}{
		{name: "first failure", base: 5 * time.Second, want: 5 * time.Second},
		{name: "second failure", base: 5 * time.Second, previous: 5 * time.Second, want: 10 * time.Second},
		{name: "continues doubling", base: 5 * time.Second, previous: 80 * time.Second, want: 160 * time.Second},
		{name: "caps before overflow", base: 5 * time.Second, previous: 160 * time.Second, want: 5 * time.Minute},
		{name: "remains capped", base: 5 * time.Second, previous: 5 * time.Minute, want: 5 * time.Minute},
		{name: "uses configured poll", base: 30 * time.Second, previous: 30 * time.Second, want: time.Minute},
		{name: "caps oversized base", base: 10 * time.Minute, want: 5 * time.Minute},
		{name: "defaults invalid base", base: 0, want: 5 * time.Second},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := nextAgentRetryDelay(test.base, test.previous); got != test.want {
				t.Fatalf("nextAgentRetryDelay(%s, %s) = %s, want %s", test.base, test.previous, got, test.want)
			}
		})
	}
}

func TestAgentControlOptionsUseBootstrapEnvironment(t *testing.T) {
	t.Setenv("NETBOX_SSOT_CONTROL_ENDPOINT", "https://netbox.example.com/api/plugins/ssot/agent/config/")
	t.Setenv("NETBOX_SSOT_AGENT_ID", "00000000-0000-4000-8000-000000000001")

	options, err := decodeAgentControl("run", nil)

	if err != nil {
		t.Fatalf("decodeAgentControl() error = %v", err)
	}
	if options.endpoint == "" || options.agentID == "" {
		t.Fatal("bootstrap environment was ignored")
	}
	if options.logLevel != slog.LevelInfo {
		t.Fatalf("default log level = %v, want info", options.logLevel)
	}
	if options.pollInterval != 5*time.Second {
		t.Fatalf("default control poll interval = %v, want 5s", options.pollInterval)
	}
}

func TestEnrollCommandCreatesPrivateFileAndReturnsOnlyPublicIdentity(t *testing.T) {
	t.Setenv("NETBOX_SSOT_ENROLLMENT_TOKEN", "nbxssot_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
	privateKeyPath := filepath.Join(t.TempDir(), "signing-key")
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		var enrollment contracts.AgentEnrollmentRequest
		if err := json.NewDecoder(request.Body).Decode(&enrollment); err != nil {
			t.Errorf("decode enrollment request: %v", err)
		}
		if len(enrollment.Providers) != 1 || enrollment.Providers[0].ProviderID != "netbox" ||
			enrollment.Providers[0].ImplementationVersion != "0.0.1" {
			t.Errorf("provider capabilities = %+v", enrollment.Providers)
		}
		publicKey, err := base64.RawURLEncoding.DecodeString(enrollment.PublicKey)
		if err != nil {
			t.Errorf("decode public key: %v", err)
		}
		digest := sha256.Sum256(publicKey)
		response.Header().Set("Content-Type", "application/json")
		response.WriteHeader(http.StatusCreated)
		json.NewEncoder(response).Encode(contracts.AgentEnrollmentResponse{
			Status: "enrolled", AgentID: "00000000-0000-4000-8000-000000000001", AgentName: "edge-1",
			ControlEndpoint: server.URL + "/config/", KeyFingerprint: hex.EncodeToString(digest[:]),
		})
	}))
	defer server.Close()
	var stdout bytes.Buffer

	err := run(context.Background(), []string{
		"enroll", "--endpoint", server.URL + "/enroll/", "--verify-tls=false",
		"--private-key-path", privateKeyPath,
	}, strings.NewReader(""), &stdout, &bytes.Buffer{})

	if err != nil {
		t.Fatalf("run(enroll) error = %v", err)
	}
	info, err := os.Stat(privateKeyPath)
	if err != nil {
		t.Fatalf("private key was not created: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("private key mode = %o, want 600", info.Mode().Perm())
	}
	privateKey, err := os.ReadFile(privateKeyPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(stdout.String(), strings.TrimSpace(string(privateKey))) ||
		strings.Contains(stdout.String(), "nbxssot_aaaaaaaa") {
		t.Fatalf("enrollment output exposed private material: %s", stdout.String())
	}
}

func TestEnrollCommandRemovesNewKeyWhenServerRejectsRequest(t *testing.T) {
	t.Setenv("NETBOX_SSOT_ENROLLMENT_TOKEN", "nbxssot_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
	privateKeyPath := filepath.Join(t.TempDir(), "signing-key")
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		http.Error(response, "rejected", http.StatusForbidden)
	}))
	defer server.Close()

	err := run(context.Background(), []string{
		"enroll", "--endpoint", server.URL + "/enroll/", "--verify-tls=false",
		"--private-key-path", privateKeyPath,
	}, strings.NewReader(""), &bytes.Buffer{}, &bytes.Buffer{})

	if err == nil {
		t.Fatal("run(enroll) accepted a rejected enrollment")
	}
	if _, statErr := os.Stat(privateKeyPath); !os.IsNotExist(statErr) {
		t.Fatalf("rejected enrollment left a private key file: %v", statErr)
	}
}

func TestWriteNewPrivateKeyReportsMissingDirectoryAndExistingTarget(t *testing.T) {
	root := t.TempDir()
	missingPath := filepath.Join(root, "missing", "signing-key")
	if err := writeNewPrivateKey(missingPath, "private"); err == nil ||
		!strings.Contains(err.Error(), "directory") || !strings.Contains(err.Error(), "does not exist") {
		t.Fatalf("missing directory error = %v", err)
	}

	existingPath := filepath.Join(root, "signing-key")
	if err := os.WriteFile(existingPath, []byte("existing\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := writeNewPrivateKey(existingPath, "replacement"); err == nil ||
		!strings.Contains(err.Error(), "target already exists") {
		t.Fatalf("existing target error = %v", err)
	}
}

func TestRotateKeyCommandAtomicallyReplacesPrivateFile(t *testing.T) {
	current, err := submission.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	directory := t.TempDir()
	privateKeyPath := filepath.Join(directory, "signing-key")
	if err := os.WriteFile(privateKeyPath, []byte(current.PrivateKey+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get(submission.SignatureHeader) == "" {
			t.Error("rotation request was not signed")
		}
		var rotation contracts.AgentKeyRotationRequest
		if err := json.NewDecoder(request.Body).Decode(&rotation); err != nil {
			t.Errorf("decode rotation request: %v", err)
		}
		publicKey, err := base64.RawURLEncoding.DecodeString(rotation.PublicKey)
		if err != nil {
			t.Errorf("decode replacement public key: %v", err)
		}
		digest := sha256.Sum256(publicKey)
		response.Header().Set("Content-Type", "application/json")
		json.NewEncoder(response).Encode(contracts.AgentKeyRotationResponse{
			Status: "rotated", KeyID: "00000000-0000-4000-8000-000000000002",
			KeyFingerprint: hex.EncodeToString(digest[:]), RetirePreviousAfter: time.Now().Add(10 * time.Minute).UTC(),
		})
	}))
	defer server.Close()

	err = run(context.Background(), []string{
		"rotate-key", "--endpoint", server.URL + "/rotate/",
		"--agent-id", "00000000-0000-4000-8000-000000000001",
		"--private-key-ref", "file://" + privateKeyPath, "--verify-tls=false",
	}, strings.NewReader(""), &bytes.Buffer{}, &bytes.Buffer{})

	if err != nil {
		t.Fatalf("run(rotate-key) error = %v", err)
	}
	replacement, err := os.ReadFile(privateKeyPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(replacement)) == current.PrivateKey {
		t.Fatal("rotation did not replace the private key")
	}
	temporaryFiles, err := filepath.Glob(filepath.Join(directory, ".netbox-ssot-signing-key-*"))
	if err != nil || len(temporaryFiles) != 0 {
		t.Fatalf("rotation left temporary private key files: %v, %v", temporaryFiles, err)
	}
}

func TestAgentControlOptionsBoundInteractivePolling(t *testing.T) {
	t.Setenv("NETBOX_SSOT_CONTROL_ENDPOINT", "https://netbox.example.com/api/plugins/ssot/agent/config/")
	t.Setenv("NETBOX_SSOT_AGENT_ID", "00000000-0000-4000-8000-000000000001")
	for _, interval := range []string{"1s", "31s"} {
		if _, err := decodeAgentControl("run", []string{"--poll-interval", interval}); err == nil {
			t.Fatalf("decodeAgentControl() accepted unsafe control interval %s", interval)
		}
	}
	options, err := decodeAgentControl("run", []string{"--poll-interval", "2s"})
	if err != nil || options.pollInterval != 2*time.Second {
		t.Fatalf("decodeAgentControl() rejected supported interval: options=%+v error=%v", options, err)
	}
}

func TestAgentControlOptionsUseLogLevelEnvironment(t *testing.T) {
	t.Setenv("NETBOX_SSOT_CONTROL_ENDPOINT", "https://netbox.example.com/api/plugins/ssot/agent/config/")
	t.Setenv("NETBOX_SSOT_AGENT_ID", "00000000-0000-4000-8000-000000000001")
	t.Setenv("NETBOX_SSOT_LOG_LEVEL", "debug")

	options, err := decodeAgentControl("run", nil)
	if err != nil {
		t.Fatalf("decodeAgentControl() error = %v", err)
	}
	if options.logLevel != slog.LevelDebug {
		t.Fatalf("environment log level = %v, want debug", options.logLevel)
	}
}

func TestConfigurationChangeLogsSafeMetadataOnly(t *testing.T) {
	var logs bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelInfo}))
	configuration := contracts.AgentConfigurationResponse{
		AgentID:     "00000000-0000-4000-8000-000000000001",
		AgentName:   "edge-1",
		GeneratedAt: time.Now().UTC(),
		Assignments: []contracts.AgentAssignment{{
			SourceID: "00000000-0000-4000-8000-000000000002", SourceName: "production",
			ProviderID: "netbox", Datasets: []string{"sites", "locations"}, IntervalSeconds: 3600,
			Revision: "revision-1", Configuration: map[string]any{"token_ref": "do-not-log"},
		}},
	}
	state := logConfigurationChanges(
		context.Background(), logger, configurationLogState{}, configuration, 5*time.Second,
	)
	initialLogs := logs.String()
	for _, expected := range []string{
		"agent configuration received",
		"source configuration received",
		"provider_id=netbox",
		"datasets=sites,locations",
		"revision=revision-1",
	} {
		if !strings.Contains(initialLogs, expected) {
			t.Fatalf("configuration logs do not contain %q: %s", expected, initialLogs)
		}
	}
	if strings.Contains(initialLogs, "do-not-log") || strings.Contains(initialLogs, "token_ref") {
		t.Fatalf("configuration logs exposed provider configuration: %s", initialLogs)
	}

	unchangedLength := logs.Len()
	state = logConfigurationChanges(context.Background(), logger, state, configuration, 5*time.Second)
	if logs.Len() != unchangedLength {
		t.Fatalf("unchanged configuration produced duplicate info logs: %s", logs.String()[unchangedLength:])
	}
	paused := false
	configuration.Assignments[0].ScheduleEnabled = &paused
	configuration.Assignments[0].SchedulePauseReason = "Waiting for review."
	state = logConfigurationChanges(context.Background(), logger, state, configuration, 5*time.Second)
	if !strings.Contains(logs.String()[unchangedLength:], "schedule_enabled=false") ||
		!strings.Contains(logs.String()[unchangedLength:], `schedule_pause_reason="Waiting for review."`) {
		t.Fatalf("schedule pause was not logged: %s", logs.String()[unchangedLength:])
	}

	configuration.Assignments[0].Revision = "revision-2"
	state = logConfigurationChanges(context.Background(), logger, state, configuration, 5*time.Second)
	if !strings.Contains(logs.String()[unchangedLength:], "change=updated") ||
		!strings.Contains(logs.String()[unchangedLength:], "revision=revision-2") {
		t.Fatalf("updated configuration was not logged: %s", logs.String()[unchangedLength:])
	}

	beforeRemoval := logs.Len()
	configuration.Assignments = nil
	logConfigurationChanges(context.Background(), logger, state, configuration, 5*time.Second)
	if !strings.Contains(logs.String()[beforeRemoval:], "source configuration removed") {
		t.Fatalf("removed assignment was not logged: %s", logs.String()[beforeRemoval:])
	}
}

func TestDaemonKeepsPausedAssignmentWithoutSchedulingIt(t *testing.T) {
	keyPair, err := submission.GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	collector := blockingCollector{started: make(chan struct{}, 1), release: make(chan struct{})}
	registry, err := providercontract.NewRegistry(collector)
	if err != nil {
		t.Fatalf("NewRegistry() error = %v", err)
	}
	const agentID = "00000000-0000-4000-8000-000000000001"
	const sourceID = "00000000-0000-4000-8000-000000000002"
	paused := false
	var fetchCount atomic.Int32
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		if request.URL.Path != "/config/" {
			http.NotFound(response, request)
			return
		}
		fetchCount.Add(1)
		json.NewEncoder(response).Encode(contracts.AgentConfigurationResponse{
			ProtocolVersion: contracts.AgentProtocolVersion, GeneratedAt: time.Now().UTC(),
			AgentID: agentID, AgentName: "edge-1", IngestEndpoint: server.URL + "/ingest/",
			CommandResultEndpoint: server.URL + "/command-results/",
			Assignments: []contracts.AgentAssignment{{
				SourceID: sourceID, SourceName: "paused source", ProviderID: "blocking",
				ExecutionMode: "agent", Datasets: []string{"objects"}, IntervalSeconds: 3600,
				Revision: "revision-1", Configuration: map[string]any{}, ScheduleEnabled: &paused,
				SchedulePauseReason: "Waiting for review.",
			}},
		})
	}))
	defer server.Close()
	runner := automation.Runner{
		Registry: registry, SecretResolver: noOpResolver{}, AgentVersion: version,
		ControlInterval: 2 * time.Second,
		ControlOptions: submission.Options{
			Endpoint: server.URL + "/config/", AgentID: agentID, PrivateKey: keyPair.PrivateKey,
			VerifyTLS: false, Timeout: 2 * time.Second,
		},
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- runDaemon(
			ctx, &bytes.Buffer{}, runner, agentID, server.URL+"/config/", 25*time.Millisecond,
			slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)),
		)
	}()
	deadline := time.Now().Add(time.Second)
	for fetchCount.Load() < 3 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if fetchCount.Load() < 3 {
		cancel()
		t.Fatalf("paused agent did not keep polling; fetch count = %d", fetchCount.Load())
	}
	select {
	case <-collector.started:
		cancel()
		t.Fatal("paused assignment started scheduled collection")
	default:
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("runDaemon() error = %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("runDaemon() did not stop")
	}
}

func TestDaemonKeepsPollingWhileCollectionRuns(t *testing.T) {
	keyPair, err := submission.GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	collector := blockingCollector{started: make(chan struct{}, 1), release: make(chan struct{})}
	registry, err := providercontract.NewRegistry(collector)
	if err != nil {
		t.Fatalf("NewRegistry() error = %v", err)
	}
	const agentID = "00000000-0000-4000-8000-000000000001"
	const sourceID = "00000000-0000-4000-8000-000000000002"
	var fetchCount atomic.Int32
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		if request.URL.Path != "/config/" {
			http.NotFound(response, request)
			return
		}
		fetchCount.Add(1)
		json.NewEncoder(response).Encode(contracts.AgentConfigurationResponse{
			ProtocolVersion: contracts.AgentProtocolVersion, GeneratedAt: time.Now().UTC(),
			AgentID: agentID, AgentName: "edge-1", IngestEndpoint: server.URL + "/ingest/",
			CommandResultEndpoint: server.URL + "/command-results/",
			CommandStatusEndpoint: server.URL + "/command-status/",
			Assignments: []contracts.AgentAssignment{{
				SourceID: sourceID, SourceName: "slow source", ProviderID: "blocking",
				ExecutionMode: "agent", Datasets: []string{"objects"}, IntervalSeconds: 3600,
				Revision: "revision-1", Configuration: map[string]any{},
			}},
		})
	}))
	defer server.Close()
	runner := automation.Runner{
		Registry: registry, SecretResolver: noOpResolver{}, AgentVersion: version,
		ControlInterval: 2 * time.Second,
		ControlOptions: submission.Options{
			Endpoint: server.URL + "/config/", AgentID: agentID, PrivateKey: keyPair.PrivateKey,
			VerifyTLS: false, Timeout: 2 * time.Second,
		},
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- runDaemon(
			ctx, &bytes.Buffer{}, runner, agentID, server.URL+"/config/", 25*time.Millisecond,
			slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)),
		)
	}()
	select {
	case <-collector.started:
	case <-time.After(time.Second):
		cancel()
		t.Fatal("scheduled collection did not start")
	}
	deadline := time.Now().Add(time.Second)
	for fetchCount.Load() < 3 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if fetchCount.Load() < 3 {
		cancel()
		t.Fatalf("configuration polls stopped during collection; fetch count = %d", fetchCount.Load())
	}
	cancel()
	close(collector.release)
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("runDaemon() error = %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("runDaemon() did not stop")
	}
}
