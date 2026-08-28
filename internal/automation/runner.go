package automation

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
	providercontract "github.com/abhi1693/netbox-ssot/internal/provider"
	"github.com/abhi1693/netbox-ssot/internal/submission"
)

type Runner struct {
	Registry             *providercontract.Registry
	SecretResolver       providercontract.SecretResolver
	ControlOptions       submission.Options
	AgentVersion         string
	ControlInterval      time.Duration
	ProviderCapabilities []contracts.AgentProviderCapability
	Logger               *slog.Logger
}

func (r Runner) Fetch(ctx context.Context) (contracts.AgentConfigurationResponse, error) {
	return r.FetchWithActiveCommands(ctx, nil)
}

func (r Runner) FetchWithActiveCommands(
	ctx context.Context,
	activeCommandIDs []string,
) (contracts.AgentConfigurationResponse, error) {
	if r.Registry == nil || r.SecretResolver == nil || r.AgentVersion == "" {
		return contracts.AgentConfigurationResponse{}, errors.New("automatic agent is not configured")
	}
	controlInterval := r.ControlInterval
	if controlInterval == 0 {
		controlInterval = 30 * time.Second
	}
	configuration, err := submission.FetchConfiguration(
		ctx,
		r.ControlOptions,
		r.AgentVersion,
		int(controlInterval/time.Second),
		activeCommandIDs,
		r.ProviderCapabilities,
	)
	if err != nil {
		return contracts.AgentConfigurationResponse{}, err
	}
	r.logger().DebugContext(ctx, "configuration request completed",
		"agent_id", configuration.AgentID,
		"assignments", len(configuration.Assignments),
	)
	return configuration, nil
}

func (r Runner) Sync(ctx context.Context) contracts.AgentSyncResult {
	configuration, err := r.Fetch(ctx)
	if err != nil {
		return contracts.AgentSyncResult{
			Succeeded: false,
			Summary:   "Agent configuration could not be fetched.",
			Results:   []contracts.SourceSyncResult{},
			Details: []contracts.CollectionMessage{{
				Code: "control_unavailable", Message: err.Error(), Retryable: true,
			}},
		}
	}
	results := make([]contracts.SourceSyncResult, 0, len(configuration.Assignments))
	completed := 0
	for _, assignment := range configuration.Assignments {
		result := r.Execute(ctx, configuration.IngestEndpoint, assignment)
		results = append(results, result)
		if result.Succeeded {
			completed++
		}
	}
	failed := len(results) - completed
	summary := fmt.Sprintf("Processed %d assigned source(s): %d succeeded and %d failed.", len(results), completed, failed)
	return contracts.AgentSyncResult{
		Succeeded:       failed == 0,
		Summary:         summary,
		AssignmentCount: len(results),
		CompletedCount:  completed,
		FailedCount:     failed,
		Results:         results,
		Details:         []contracts.CollectionMessage{},
	}
}

func (r Runner) Execute(
	ctx context.Context,
	ingestEndpoint string,
	assignment contracts.AgentAssignment,
) contracts.SourceSyncResult {
	return r.execute(ctx, ingestEndpoint, assignment, "")
}

func (r Runner) execute(
	ctx context.Context,
	ingestEndpoint string,
	assignment contracts.AgentAssignment,
	requestedRunID string,
) contracts.SourceSyncResult {
	startedAt := time.Now()
	result := contracts.SourceSyncResult{
		SourceID:   assignment.SourceID,
		SourceName: assignment.SourceName,
		Details:    []contracts.CollectionMessage{},
	}
	collector, found := r.Registry.Get(assignment.ProviderID)
	if !found {
		result.Summary = "Assigned provider is not compiled into this agent."
		result.Details = []contracts.CollectionMessage{{
			Code: "provider_unavailable", Message: result.Summary, Retryable: false,
		}}
		r.logFailure(ctx, assignment, result, startedAt)
		return result
	}
	runID := requestedRunID
	if runID == "" {
		var err error
		runID, err = newRunID()
		if err != nil {
			result.Summary = "A collection run ID could not be generated."
			result.Details = []contracts.CollectionMessage{{Code: "run_id_failed", Message: result.Summary}}
			r.logFailure(ctx, assignment, result, startedAt)
			return result
		}
	}
	result.RunID = runID
	logger := r.logger().With(
		"source_id", assignment.SourceID,
		"source_name", assignment.SourceName,
		"provider_id", assignment.ProviderID,
		"run_id", runID,
	)
	logger.InfoContext(ctx, "source synchronization started")
	batch := collector.Collect(ctx, contracts.CollectionRequest{
		RunID:         runID,
		SourceID:      assignment.SourceID,
		ProviderID:    assignment.ProviderID,
		ExecutionMode: assignment.ExecutionMode,
		Datasets:      assignment.Datasets,
		Scope:         assignment.Scope,
		Configuration: assignment.Configuration,
	}, r.SecretResolver)
	result.CollectionState = batch.State
	result.Details = batch.Messages
	logger.DebugContext(ctx, "source collection completed",
		"state", batch.State,
		"observations", len(batch.Observations),
		"messages", len(batch.Messages),
	)
	body, err := json.Marshal(batch)
	if err != nil {
		result.Summary = "Collected data could not be encoded for submission."
		r.logFailure(ctx, assignment, result, startedAt)
		return result
	}
	submitOptions := r.ControlOptions
	submitOptions.Endpoint = ingestEndpoint
	submitted := submission.Submit(ctx, submitOptions, body)
	result.SubmissionState = submitted.Status
	if !submitted.Succeeded {
		result.Summary = submitted.Summary
		result.Details = append(result.Details, submitted.Details...)
		r.logFailure(ctx, assignment, result, startedAt)
		return result
	}
	if batch.State != "complete" {
		result.Summary = "Collection did not complete; failure evidence was submitted."
		r.logFailure(ctx, assignment, result, startedAt)
		return result
	}
	result.Succeeded = true
	result.Summary = "Collection completed and was accepted by NetBox."
	logger.InfoContext(ctx, "source synchronization completed",
		"collection_state", result.CollectionState,
		"submission_state", result.SubmissionState,
		"observations", len(batch.Observations),
		"duration", time.Since(startedAt),
	)
	return result
}

func (r Runner) ExecuteCommand(
	ctx context.Context,
	ingestEndpoint string,
	command contracts.AgentCommand,
) contracts.AgentCommandResult {
	result := contracts.AgentCommandResult{
		CommandID:   command.CommandID,
		SourceID:    command.Assignment.SourceID,
		Kind:        command.Kind,
		Details:     []contracts.CollectionMessage{},
		CompletedAt: time.Now().UTC(),
	}
	if command.Kind == "test_connection" {
		collector, found := r.Registry.Get(command.Assignment.ProviderID)
		if !found {
			result.Summary = "Assigned provider is not compiled into this agent."
			result.Details = []contracts.CollectionMessage{{
				Code: "provider_unavailable", Message: result.Summary, Retryable: false,
			}}
			return result
		}
		tested := collector.TestConnection(ctx, contracts.ConnectionTestRequest{
			SourceID:      command.Assignment.SourceID,
			ProviderID:    command.Assignment.ProviderID,
			ExecutionMode: command.Assignment.ExecutionMode,
			Configuration: command.Assignment.Configuration,
		}, r.SecretResolver)
		result.Succeeded = tested.Succeeded
		result.Summary = tested.Summary
		result.Details = tested.Details
		result.CompletedAt = time.Now().UTC()
		return result
	}
	if command.Kind != "run_now" {
		result.Summary = "Agent command kind is unsupported."
		result.Details = []contracts.CollectionMessage{{Code: "unsupported_command", Message: result.Summary}}
		return result
	}
	synced := r.execute(ctx, ingestEndpoint, command.Assignment, command.CommandID)
	result.Succeeded = synced.Succeeded
	result.Summary = synced.Summary
	result.RunID = synced.RunID
	result.CollectionState = synced.CollectionState
	result.SubmissionState = synced.SubmissionState
	result.Details = synced.Details
	result.CompletedAt = time.Now().UTC()
	return result
}

func (r Runner) ReportCommandResult(
	ctx context.Context,
	endpoint string,
	result contracts.AgentCommandResult,
) error {
	options := r.ControlOptions
	options.Endpoint = endpoint
	return submission.SubmitCommandResult(ctx, options, result)
}

func (r Runner) ReportCommandStatus(
	ctx context.Context,
	endpoint string,
	update contracts.AgentCommandStatusUpdate,
) error {
	if endpoint == "" {
		return nil
	}
	options := r.ControlOptions
	options.Endpoint = endpoint
	return submission.SubmitCommandStatus(ctx, options, update)
}

func (r Runner) logger() *slog.Logger {
	if r.Logger == nil {
		return slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	return r.Logger
}

func (r Runner) logFailure(
	ctx context.Context,
	assignment contracts.AgentAssignment,
	result contracts.SourceSyncResult,
	startedAt time.Time,
) {
	r.logger().WarnContext(ctx, "source synchronization failed",
		"source_id", assignment.SourceID,
		"source_name", assignment.SourceName,
		"provider_id", assignment.ProviderID,
		"run_id", result.RunID,
		"collection_state", result.CollectionState,
		"submission_state", result.SubmissionState,
		"retryable", hasRetryableDetail(result.Details),
		"summary", result.Summary,
		"duration", time.Since(startedAt),
	)
}

func hasRetryableDetail(details []contracts.CollectionMessage) bool {
	for _, detail := range details {
		if detail.Retryable {
			return true
		}
	}
	return false
}

func newRunID() (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	value[6] = (value[6] & 0x0f) | 0x40
	value[8] = (value[8] & 0x3f) | 0x80
	return fmt.Sprintf(
		"%08x-%04x-%04x-%04x-%012x",
		value[0:4],
		value[4:6],
		value[6:8],
		value[8:10],
		value[10:16],
	), nil
}
