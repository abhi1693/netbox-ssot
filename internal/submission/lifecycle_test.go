package submission

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

func TestEnrollValidatesBoundControlEndpoint(t *testing.T) {
	keyPair, err := GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get(AgentHeader) != "" || request.Header.Get(SignatureHeader) != "" {
			t.Error("enrollment unexpectedly sent agent signature headers")
		}
		var received contracts.AgentEnrollmentRequest
		if err := json.NewDecoder(request.Body).Decode(&received); err != nil || received.Token != "one-time-token" {
			t.Errorf("invalid enrollment request: %+v, %v", received, err)
		}
		response.Header().Set("Content-Type", "application/json")
		response.WriteHeader(http.StatusCreated)
		json.NewEncoder(response).Encode(contracts.AgentEnrollmentResponse{
			Status: "enrolled", AgentID: "00000000-0000-4000-8000-000000000001", AgentName: "edge-1",
			ControlEndpoint: server.URL + "/config/", KeyFingerprint: publicKeyFingerprint(received.PublicKey),
		})
	}))
	defer server.Close()

	result, err := Enroll(context.Background(), Options{
		Endpoint: server.URL + "/enroll/", VerifyTLS: false, Timeout: time.Second,
	}, contracts.AgentEnrollmentRequest{
		Token: "one-time-token", PublicKey: keyPair.PublicKey,
		AgentVersion: "0.6.3-alpha.0", ProtocolVersion: "1.1",
	}, "0.6.3-alpha.0")

	if err != nil || result.AgentName != "edge-1" {
		t.Fatalf("Enroll() = %+v, %v", result, err)
	}
}

func TestRotateKeySignsWithCurrentKey(t *testing.T) {
	current, err := GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	replacement, err := GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	var server *httptest.Server
	server = httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get(AgentHeader) == "" || request.Header.Get(SignatureHeader) == "" ||
			request.Header.Get(TimestampHeader) == "" {
			t.Error("rotation request was not signed")
		}
		var received contracts.AgentKeyRotationRequest
		if err := json.NewDecoder(request.Body).Decode(&received); err != nil {
			t.Errorf("invalid rotation request: %v", err)
		}
		response.Header().Set("Content-Type", "application/json")
		json.NewEncoder(response).Encode(contracts.AgentKeyRotationResponse{
			Status: "rotated", KeyID: "00000000-0000-4000-8000-000000000002",
			KeyFingerprint:      publicKeyFingerprint(received.PublicKey),
			RetirePreviousAfter: time.Now().Add(10 * time.Minute).UTC(),
		})
	}))
	defer server.Close()

	result, err := RotateKey(context.Background(), Options{
		Endpoint: server.URL + "/rotate/", AgentID: "00000000-0000-4000-8000-000000000001",
		PrivateKey: current.PrivateKey, VerifyTLS: false, Timeout: time.Second,
	}, contracts.AgentKeyRotationRequest{
		PublicKey: replacement.PublicKey, AgentVersion: "0.6.3-alpha.0",
	}, "0.6.3-alpha.0")

	if err != nil || result.Status != "rotated" {
		t.Fatalf("RotateKey() = %+v, %v", result, err)
	}
}
