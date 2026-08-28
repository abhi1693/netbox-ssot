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

func TestSubmitCommandResultAuthenticatesAndValidatesAcknowledgement(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	const agentID = "00000000-0000-4000-8000-000000000001"
	result := contracts.AgentCommandResult{
		CommandID: "00000000-0000-4000-8000-000000000002", SourceID: "00000000-0000-4000-8000-000000000003",
		Kind: "test_connection", Succeeded: true, Summary: "Connected.",
		Details: []contracts.CollectionMessage{}, CompletedAt: time.Now().UTC(),
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		var submitted contracts.AgentCommandResult
		if decodeErr := json.NewDecoder(request.Body).Decode(&submitted); decodeErr != nil {
			t.Errorf("command result is invalid: %v", decodeErr)
		}
		encodedBody, _ := json.Marshal(submitted)
		timestamp, _ := strconv.ParseInt(request.Header.Get(TimestampHeader), 10, 64)
		signature, _ := base64.RawURLEncoding.DecodeString(request.Header.Get(SignatureHeader))
		publicKey, _ := base64.RawURLEncoding.DecodeString(keyPair.PublicKey)
		if request.Method != http.MethodPost || request.Header.Get(AgentHeader) != agentID ||
			!ed25519.Verify(publicKey, signingPayload(agentID, timestamp, encodedBody), signature) {
			t.Error("command result was not correctly authenticated")
		}
		json.NewEncoder(response).Encode(map[string]string{"status": "accepted", "command_id": submitted.CommandID})
	}))
	defer server.Close()

	err = SubmitCommandResult(context.Background(), Options{
		Endpoint: server.URL + "/", AgentID: agentID, PrivateKey: keyPair.PrivateKey, VerifyTLS: false, Timeout: 5 * time.Second,
	}, result)
	if err != nil {
		t.Fatalf("SubmitCommandResult() error = %v", err)
	}
}

func TestSubmitCommandStatusAuthenticatesAndValidatesAcknowledgement(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	const agentID = "00000000-0000-4000-8000-000000000001"
	update := contracts.AgentCommandStatusUpdate{
		CommandID: "00000000-0000-4000-8000-000000000002",
		SourceID:  "00000000-0000-4000-8000-000000000003",
		Kind:      "run_now", State: "running", Summary: "Agent started executing the command.",
		OccurredAt: time.Now().UTC(),
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		var submitted contracts.AgentCommandStatusUpdate
		if decodeErr := json.NewDecoder(request.Body).Decode(&submitted); decodeErr != nil {
			t.Errorf("command status is invalid: %v", decodeErr)
		}
		encodedBody, _ := json.Marshal(submitted)
		timestamp, _ := strconv.ParseInt(request.Header.Get(TimestampHeader), 10, 64)
		signature, _ := base64.RawURLEncoding.DecodeString(request.Header.Get(SignatureHeader))
		publicKey, _ := base64.RawURLEncoding.DecodeString(keyPair.PublicKey)
		if !ed25519.Verify(publicKey, signingPayload(agentID, timestamp, encodedBody), signature) {
			t.Error("command status was not correctly authenticated")
		}
		json.NewEncoder(response).Encode(map[string]string{"status": "accepted", "command_id": submitted.CommandID})
	}))
	defer server.Close()
	err = SubmitCommandStatus(context.Background(), Options{
		Endpoint: server.URL + "/", AgentID: agentID, PrivateKey: keyPair.PrivateKey,
		VerifyTLS: false, Timeout: 5 * time.Second,
	}, update)
	if err != nil {
		t.Fatalf("SubmitCommandStatus() error = %v", err)
	}
}
