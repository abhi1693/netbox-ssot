package submission

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

func TestFetchConfigurationAuthenticatesAndValidatesAssignments(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	const agentID = "00000000-0000-4000-8000-000000000001"
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		var body contracts.AgentConfigurationRequest
		if decodeErr := json.NewDecoder(request.Body).Decode(&body); decodeErr != nil {
			t.Errorf("configuration request is invalid: %v", decodeErr)
		}
		encodedBody, _ := json.Marshal(body)
		timestamp, _ := strconv.ParseInt(request.Header.Get(TimestampHeader), 10, 64)
		signature, _ := base64.RawURLEncoding.DecodeString(request.Header.Get(SignatureHeader))
		publicKey, _ := base64.RawURLEncoding.DecodeString(keyPair.PublicKey)
		if request.Method != http.MethodPost || request.Header.Get(AgentHeader) != agentID ||
			!ed25519.Verify(publicKey, signingPayload(agentID, timestamp, encodedBody), signature) {
			t.Error("configuration request was not correctly authenticated")
		}
		if body.ControlIntervalSeconds != 5 {
			t.Errorf("control interval = %d, want 5", body.ControlIntervalSeconds)
		}
		if len(body.Providers) != 1 || body.Providers[0].ProviderID != "netbox" ||
			body.Providers[0].ImplementationVersion != "0.0.2" || body.Providers[0].ContractVersion != "1.0" {
			t.Errorf("provider capabilities = %+v", body.Providers)
		}
		response.Header().Set("Content-Type", "application/json")
		json.NewEncoder(response).Encode(contracts.AgentConfigurationResponse{
			ProtocolVersion:        contracts.AgentProtocolVersion,
			GeneratedAt:            time.Now().UTC(),
			AgentID:                agentID,
			AgentName:              "edge-1",
			IngestEndpoint:         server.URL + "/api/plugins/ssot/ingest/batches/",
			CommandResultEndpoint:  server.URL + "/api/plugins/ssot/agent/commands/results/",
			ControlIntervalSeconds: 5,
			Assignments: []contracts.AgentAssignment{{
				SourceID:        "00000000-0000-4000-8000-000000000002",
				SourceName:      "production",
				ProviderID:      "netbox",
				ExecutionMode:   "agent",
				Datasets:        []string{"sites"},
				Scope:           []contracts.ScopeDimension{},
				Configuration:   map[string]any{"token_ref": "env://NETBOX_TOKEN"},
				IntervalSeconds: 3600,
				Revision:        "revision-1",
			}},
		})
	}))
	defer server.Close()

	configuration, err := FetchConfiguration(context.Background(), Options{
		Endpoint:   server.URL + "/api/plugins/ssot/agent/config/",
		AgentID:    agentID,
		PrivateKey: keyPair.PrivateKey,
		VerifyTLS:  false,
		Timeout:    5 * time.Second,
	}, "0.6.0", 5, nil, []contracts.AgentProviderCapability{{
		ProviderID: "netbox", ImplementationVersion: "0.0.2", ContractVersion: "1.0",
	}})
	if err != nil {
		t.Fatalf("FetchConfiguration() error = %v", err)
	}
	if len(configuration.Assignments) != 1 || configuration.Assignments[0].Revision != "revision-1" {
		t.Fatalf("configuration = %+v", configuration)
	}
	if configuration.ControlIntervalSeconds != 5 {
		t.Fatalf("control interval = %d, want 5", configuration.ControlIntervalSeconds)
	}
}

func TestFetchConfigurationRejectsCrossOriginIngest(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	const agentID = "00000000-0000-4000-8000-000000000001"
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(response).Encode(contracts.AgentConfigurationResponse{
			ProtocolVersion:       contracts.AgentProtocolVersion,
			GeneratedAt:           time.Now().UTC(),
			AgentID:               agentID,
			AgentName:             "edge-1",
			IngestEndpoint:        "https://untrusted.example.test/ingest/",
			CommandResultEndpoint: server.URL + "/command-results/",
			Assignments:           []contracts.AgentAssignment{},
		})
	}))
	defer server.Close()

	_, err = FetchConfiguration(context.Background(), Options{
		Endpoint:   server.URL + "/api/plugins/ssot/agent/config/",
		AgentID:    agentID,
		PrivateKey: keyPair.PrivateKey,
		VerifyTLS:  false,
		Timeout:    5 * time.Second,
	}, "0.6.0", 5, nil, nil)
	if err == nil {
		t.Fatal("FetchConfiguration() accepted a cross-origin ingest endpoint")
	}
}
