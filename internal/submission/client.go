package submission

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

const (
	AgentHeader     = "X-NetBox-SSoT-Agent"
	TimestampHeader = "X-NetBox-SSoT-Timestamp"
	SignatureHeader = "X-NetBox-SSoT-Signature"
	maxBatchBytes   = 64 << 20
	maxResponseSize = 1 << 20
)

type Options struct {
	Endpoint          string
	AgentID           string
	PrivateKey        string
	VerifyTLS         bool
	AllowInsecureHTTP bool
	Timeout           time.Duration
}

type ingestResponse struct {
	Status           string `json:"status"`
	RunID            string `json:"run_id"`
	ObservationCount int    `json:"observation_count"`
	PayloadDigest    string `json:"payload_digest"`
}

func Submit(ctx context.Context, options Options, body []byte) contracts.SubmissionResult {
	if err := validateOptions(options); err != nil {
		return failed("invalid_submission_configuration", "Submission configuration is invalid.", false)
	}
	if len(body) == 0 || len(body) > maxBatchBytes || !json.Valid(body) {
		return failed("invalid_batch", "Observation batch is not valid JSON or exceeds the size limit.", false)
	}
	var batch contracts.ObservationBatch
	if err := json.Unmarshal(body, &batch); err != nil || batch.RunID == "" || batch.SourceID == "" || batch.ProviderID == "" || len(batch.Datasets) == 0 {
		return failed("invalid_batch", "Observation batch envelope is incomplete.", false)
	}

	timestamp := time.Now().Unix()
	signature, err := signBody(options.PrivateKey, options.AgentID, timestamp, body)
	if err != nil {
		return failed("invalid_signing_key", "Agent signing key is invalid.", false)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, options.Endpoint, bytes.NewReader(body))
	if err != nil {
		return failed("invalid_submission_configuration", "Submission configuration is invalid.", false)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "netbox-ssot-agent/0.1")
	request.Header.Set(AgentHeader, options.AgentID)
	request.Header.Set(TimestampHeader, fmt.Sprintf("%d", timestamp))
	request.Header.Set(SignatureHeader, signature)

	client := newHTTPClient(options)
	response, err := client.Do(request)
	if err != nil {
		return failed("submission_failed", "Signed batch could not be submitted.", true)
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		retryable := response.StatusCode == http.StatusTooManyRequests || response.StatusCode >= 500
		return failed("http_status", fmt.Sprintf("Ingest endpoint returned HTTP %d.", response.StatusCode), retryable)
	}

	var accepted ingestResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&accepted); err != nil || accepted.RunID != batch.RunID || accepted.ObservationCount != len(batch.Observations) || (accepted.Status != "accepted" && accepted.Status != "duplicate") {
		return failed("invalid_response", "Ingest endpoint returned an invalid acknowledgement.", false)
	}
	return contracts.SubmissionResult{
		Succeeded:        true,
		Status:           accepted.Status,
		Summary:          "Observation batch accepted by the NetBox SSoT plugin.",
		RunID:            accepted.RunID,
		ObservationCount: accepted.ObservationCount,
		PayloadDigest:    accepted.PayloadDigest,
		Details:          []contracts.CollectionMessage{},
	}
}

func newHTTPClient(options Options) *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: !options.VerifyTLS} //nolint:gosec
	return &http.Client{
		Transport: transport,
		Timeout:   options.Timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func validateOptions(options Options) error {
	if err := validateEndpoint(options); err != nil {
		return err
	}
	if options.AgentID == "" || options.PrivateKey == "" {
		return errors.New("invalid submission options")
	}
	return nil
}

func validateEndpoint(options Options) error {
	endpoint, err := url.Parse(options.Endpoint)
	if err != nil || endpoint.Host == "" || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return errors.New("invalid ingest endpoint")
	}
	if endpoint.Scheme != "https" && !(endpoint.Scheme == "http" && options.AllowInsecureHTTP) {
		return errors.New("ingest endpoint must use HTTPS")
	}
	if !strings.HasSuffix(endpoint.Path, "/") || options.Timeout < time.Second || options.Timeout > 2*time.Minute {
		return errors.New("invalid submission options")
	}
	return nil
}

func failed(code string, summary string, retryable bool) contracts.SubmissionResult {
	return contracts.SubmissionResult{
		Succeeded: false,
		Summary:   summary,
		Details:   []contracts.CollectionMessage{{Code: code, Message: summary, Retryable: retryable}},
	}
}
