package submission

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

var uuidPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$`)

func Enroll(
	ctx context.Context,
	options Options,
	requestBody contracts.AgentEnrollmentRequest,
	agentVersion string,
) (contracts.AgentEnrollmentResponse, error) {
	fingerprint := publicKeyFingerprint(requestBody.PublicKey)
	if err := validateEndpoint(options); err != nil || requestBody.Token == "" || fingerprint == "" {
		return contracts.AgentEnrollmentResponse{}, errors.New("agent enrollment configuration is invalid")
	}
	body, err := json.Marshal(requestBody)
	if err != nil {
		return contracts.AgentEnrollmentResponse{}, errors.New("agent enrollment request could not be encoded")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, options.Endpoint, bytes.NewReader(body))
	if err != nil {
		return contracts.AgentEnrollmentResponse{}, errors.New("agent enrollment request could not be created")
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "netbox-ssot-agent/"+agentVersion)
	response, err := newHTTPClient(options).Do(request)
	if err != nil {
		return contracts.AgentEnrollmentResponse{}, errors.New("agent enrollment request failed")
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return contracts.AgentEnrollmentResponse{}, fmt.Errorf("agent enrollment endpoint returned HTTP %d", response.StatusCode)
	}
	var enrolled contracts.AgentEnrollmentResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&enrolled); err != nil || enrolled.Status != "enrolled" ||
		!uuidPattern.MatchString(enrolled.AgentID) || enrolled.AgentName == "" ||
		!sameOrigin(options.Endpoint, enrolled.ControlEndpoint) ||
		enrolled.KeyFingerprint != fingerprint {
		return contracts.AgentEnrollmentResponse{}, errors.New("agent enrollment response is invalid")
	}
	return enrolled, nil
}

func RotateKey(
	ctx context.Context,
	options Options,
	requestBody contracts.AgentKeyRotationRequest,
	agentVersion string,
) (contracts.AgentKeyRotationResponse, error) {
	fingerprint := publicKeyFingerprint(requestBody.PublicKey)
	if err := validateOptions(options); err != nil || fingerprint == "" {
		return contracts.AgentKeyRotationResponse{}, errors.New("agent key rotation configuration is invalid")
	}
	body, err := json.Marshal(requestBody)
	if err != nil {
		return contracts.AgentKeyRotationResponse{}, errors.New("agent key rotation request could not be encoded")
	}
	timestamp := time.Now().Unix()
	signature, err := signBody(options.PrivateKey, options.AgentID, timestamp, body)
	if err != nil {
		return contracts.AgentKeyRotationResponse{}, errors.New("agent signing key is invalid")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, options.Endpoint, bytes.NewReader(body))
	if err != nil {
		return contracts.AgentKeyRotationResponse{}, errors.New("agent key rotation request could not be created")
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "netbox-ssot-agent/"+agentVersion)
	request.Header.Set(AgentHeader, options.AgentID)
	request.Header.Set(TimestampHeader, fmt.Sprintf("%d", timestamp))
	request.Header.Set(SignatureHeader, signature)
	response, err := newHTTPClient(options).Do(request)
	if err != nil {
		return contracts.AgentKeyRotationResponse{}, errors.New("agent key rotation request failed")
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return contracts.AgentKeyRotationResponse{}, fmt.Errorf("agent key rotation endpoint returned HTTP %d", response.StatusCode)
	}
	var rotated contracts.AgentKeyRotationResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&rotated); err != nil || (rotated.Status != "rotated" && rotated.Status != "duplicate") ||
		!uuidPattern.MatchString(rotated.KeyID) ||
		rotated.KeyFingerprint != fingerprint ||
		rotated.RetirePreviousAfter.IsZero() {
		return contracts.AgentKeyRotationResponse{}, errors.New("agent key rotation response is invalid")
	}
	return rotated, nil
}

func publicKeyFingerprint(value string) string {
	decoded, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil || len(decoded) != 32 {
		return ""
	}
	digest := sha256.Sum256(decoded)
	return hex.EncodeToString(digest[:])
}
