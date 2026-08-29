package submission

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestSubmitSignsBatchAndAcceptsAcknowledgement(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	const agentID = "00000000-0000-4000-8000-000000000001"
	const runID = "00000000-0000-4000-8000-000000000002"
	body := []byte(`{"run_id":"` + runID + `","source_id":"00000000-0000-4000-8000-000000000003","provider_id":"netbox","provider_version":"0.1.0","contract_version":"1.0","state":"complete","started_at":"2026-08-28T00:00:00Z","completed_at":"2026-08-28T00:00:01Z","datasets":["sites"],"scope":[],"observations":[],"messages":[],"completeness_token":"complete"}`)

	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.Header.Get(AgentHeader) != agentID {
			t.Errorf("unexpected request method or agent header")
		}
		timestamp, parseErr := strconv.ParseInt(request.Header.Get(TimestampHeader), 10, 64)
		if parseErr != nil {
			t.Errorf("timestamp header is invalid: %v", parseErr)
		}
		signature, decodeErr := base64.RawURLEncoding.DecodeString(request.Header.Get(SignatureHeader))
		if decodeErr != nil {
			t.Errorf("signature header is invalid: %v", decodeErr)
		}
		publicKey, decodeErr := base64.RawURLEncoding.DecodeString(keyPair.PublicKey)
		if decodeErr != nil || !ed25519.Verify(publicKey, signingPayload(agentID, timestamp, body), signature) {
			t.Error("batch signature did not verify")
		}
		response.Header().Set("Content-Type", "application/json")
		json.NewEncoder(response).Encode(map[string]any{
			"status":            "accepted",
			"run_id":            runID,
			"observation_count": 0,
			"payload_digest":    "digest",
		})
	}))
	defer server.Close()

	result := Submit(context.Background(), Options{
		Endpoint:   server.URL + "/api/plugins/netbox-ssot/ingest/batches/",
		AgentID:    agentID,
		PrivateKey: keyPair.PrivateKey,
		VerifyTLS:  false,
		Timeout:    5 * time.Second,
	}, body)
	if !result.Succeeded || result.Status != "accepted" || result.RunID != runID {
		t.Fatalf("result = %+v", result)
	}
}

func TestSubmitRejectsInsecureHTTPByDefault(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	result := Submit(context.Background(), Options{
		Endpoint:   "http://netbox.example.test/api/plugins/netbox-ssot/ingest/batches/",
		AgentID:    "00000000-0000-4000-8000-000000000001",
		PrivateKey: keyPair.PrivateKey,
		VerifyTLS:  true,
		Timeout:    5 * time.Second,
	}, []byte(`{"run_id":"run","source_id":"source","provider_id":"netbox","datasets":["sites"]}`))
	if result.Succeeded || len(result.Details) != 1 || result.Details[0].Code != "invalid_submission_configuration" {
		t.Fatalf("result = %+v", result)
	}
}

func TestSubmitReportsSafeJSONRejectionReason(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair() error = %v", err)
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		response.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(response).Encode(map[string]string{
			"code": "invalid_batch", "message": "Request body does not conform to the observation contract.",
		})
	}))
	defer server.Close()
	body := []byte(`{"run_id":"00000000-0000-4000-8000-000000000002","source_id":"00000000-0000-4000-8000-000000000003","provider_id":"netbox","provider_version":"0.1.0","contract_version":"1.0","state":"complete","started_at":"2026-08-28T00:00:00Z","completed_at":"2026-08-28T00:00:01Z","datasets":["sites"],"scope":[],"observations":[],"messages":[],"completeness_token":"complete"}`)

	result := Submit(context.Background(), Options{
		Endpoint: server.URL + "/", AgentID: "00000000-0000-4000-8000-000000000001",
		PrivateKey: keyPair.PrivateKey, VerifyTLS: false, Timeout: 5 * time.Second,
	}, body)
	if result.Succeeded || !strings.Contains(result.Summary, "does not conform") {
		t.Fatalf("result = %+v", result)
	}
}
