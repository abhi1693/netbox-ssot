package automation

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
	providercontract "github.com/abhi1693/netbox-ssot/internal/provider"
	"github.com/abhi1693/netbox-ssot/internal/submission"
)

type fakeCollector struct{}

func (fakeCollector) Manifest() (contracts.ProviderManifest, error) {
	return contracts.ProviderManifest{
		ProviderID:      "demo",
		ContractVersion: contracts.ContractVersion,
		AgentCompatibility: contracts.AgentCompatibility{
			ProtocolVersion: contracts.AgentProtocolVersion,
			CollectorID:     "demo",
		},
	}, nil
}

func (fakeCollector) TestConnection(
	context.Context,
	contracts.ConnectionTestRequest,
	providercontract.SecretResolver,
) contracts.ConnectionTestResult {
	return contracts.ConnectionTestResult{Succeeded: true}
}

func (fakeCollector) Collect(
	_ context.Context,
	request contracts.CollectionRequest,
	_ providercontract.SecretResolver,
) contracts.ObservationBatch {
	now := time.Now().UTC()
	return contracts.ObservationBatch{
		RunID:             request.RunID,
		SourceID:          request.SourceID,
		ProviderID:        request.ProviderID,
		ProviderVersion:   "test",
		ContractVersion:   contracts.ContractVersion,
		State:             "complete",
		StartedAt:         now,
		CompletedAt:       now,
		Datasets:          request.Datasets,
		Scope:             request.Scope,
		Observations:      []contracts.Observation{},
		Messages:          []contracts.CollectionMessage{},
		CompletenessToken: "complete",
	}
}

type fakeResolver struct{}

func (fakeResolver) Resolve(context.Context, string) (string, error) {
	return "unused", nil
}

func TestRunnerFetchesCollectsAndSubmitsAssignedSource(t *testing.T) {
	var logs bytes.Buffer
	keyPair, err := submission.GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	const agentID = "00000000-0000-4000-8000-000000000001"
	const sourceID = "00000000-0000-4000-8000-000000000002"
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		if request.URL.Path == "/config/" {
			json.NewEncoder(response).Encode(contracts.AgentConfigurationResponse{
				ProtocolVersion:        contracts.AgentProtocolVersion,
				GeneratedAt:            time.Now().UTC(),
				AgentID:                agentID,
				AgentName:              "edge-1",
				IngestEndpoint:         server.URL + "/ingest/",
				CommandResultEndpoint:  server.URL + "/command-results/",
				ControlIntervalSeconds: 5,
				Assignments: []contracts.AgentAssignment{{
					SourceID:        sourceID,
					SourceName:      "production",
					ProviderID:      "demo",
					ExecutionMode:   "agent",
					Datasets:        []string{"objects"},
					Scope:           []contracts.ScopeDimension{},
					Configuration:   map[string]any{"token_ref": "do-not-log"},
					IntervalSeconds: 3600,
					Revision:        "revision-1",
				}},
			})
			return
		}
		var batch contracts.ObservationBatch
		if decodeErr := json.NewDecoder(request.Body).Decode(&batch); decodeErr != nil {
			t.Errorf("submitted batch is invalid: %v", decodeErr)
		}
		response.WriteHeader(http.StatusCreated)
		json.NewEncoder(response).Encode(map[string]any{
			"status": "accepted", "run_id": batch.RunID, "observation_count": 0, "payload_digest": "digest",
		})
	}))
	defer server.Close()

	registry, err := providercontract.NewRegistry(fakeCollector{})
	if err != nil {
		t.Fatalf("NewRegistry() error = %v", err)
	}
	runner := Runner{
		Registry:       registry,
		SecretResolver: fakeResolver{},
		ControlOptions: submission.Options{
			Endpoint:   server.URL + "/config/",
			AgentID:    agentID,
			PrivateKey: keyPair.PrivateKey,
			VerifyTLS:  false,
			Timeout:    5 * time.Second,
		},
		AgentVersion: "test",
		Logger:       slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug})),
	}

	result := runner.Sync(context.Background())

	if !result.Succeeded || result.CompletedCount != 1 || result.Results[0].SourceID != sourceID {
		t.Fatalf("result = %+v", result)
	}
	for _, message := range []string{
		"configuration request completed",
		"source synchronization started",
		"source collection completed",
		"source synchronization completed",
	} {
		if !strings.Contains(logs.String(), message) {
			t.Fatalf("logs do not contain %q: %s", message, logs.String())
		}
	}
	if strings.Contains(logs.String(), "do-not-log") {
		t.Fatalf("provider configuration was written to logs: %s", logs.String())
	}
}

func TestRunnerExecutesAgentCommands(t *testing.T) {
	keyPair, err := submission.GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	var collectedRunID string
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		var batch contracts.ObservationBatch
		if decodeErr := json.NewDecoder(request.Body).Decode(&batch); decodeErr != nil {
			t.Errorf("submitted batch is invalid: %v", decodeErr)
		}
		collectedRunID = batch.RunID
		response.WriteHeader(http.StatusCreated)
		json.NewEncoder(response).Encode(map[string]any{
			"status": "accepted", "run_id": batch.RunID, "observation_count": 0, "payload_digest": "digest",
		})
	}))
	defer server.Close()
	registry, err := providercontract.NewRegistry(fakeCollector{})
	if err != nil {
		t.Fatalf("NewRegistry() error = %v", err)
	}
	runner := Runner{
		Registry:       registry,
		SecretResolver: fakeResolver{},
		ControlOptions: submission.Options{
			AgentID: "00000000-0000-4000-8000-000000000001", PrivateKey: keyPair.PrivateKey,
			VerifyTLS: false, Timeout: 5 * time.Second,
		},
	}
	assignment := contracts.AgentAssignment{
		SourceID: "00000000-0000-4000-8000-000000000002", SourceName: "production",
		ProviderID: "demo", ExecutionMode: "agent", Datasets: []string{"objects"},
	}
	testResult := runner.ExecuteCommand(context.Background(), server.URL, contracts.AgentCommand{
		CommandID: "00000000-0000-4000-8000-000000000003", Kind: "test_connection", Assignment: assignment,
	})
	if !testResult.Succeeded {
		t.Fatalf("test connection result = %+v", testResult)
	}
	runResult := runner.ExecuteCommand(context.Background(), server.URL+"/", contracts.AgentCommand{
		CommandID: "00000000-0000-4000-8000-000000000004", Kind: "run_now", Assignment: assignment,
	})
	if !runResult.Succeeded || runResult.RunID != collectedRunID || collectedRunID != runResult.CommandID {
		t.Fatalf("run now result = %+v, collected run ID = %q", runResult, collectedRunID)
	}
}
