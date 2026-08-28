package submission

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

type commandResultResponse struct {
	Status    string `json:"status"`
	CommandID string `json:"command_id"`
}

func SubmitCommandResult(ctx context.Context, options Options, result contracts.AgentCommandResult) error {
	if err := validateOptions(options); err != nil {
		return errors.New("command result submission configuration is invalid")
	}
	body, err := json.Marshal(result)
	if err != nil {
		return errors.New("command result could not be encoded")
	}
	timestamp := time.Now().Unix()
	signature, err := signBody(options.PrivateKey, options.AgentID, timestamp, body)
	if err != nil {
		return errors.New("agent signing key is invalid")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, options.Endpoint, bytes.NewReader(body))
	if err != nil {
		return errors.New("command result request could not be created")
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set(AgentHeader, options.AgentID)
	request.Header.Set(TimestampHeader, fmt.Sprintf("%d", timestamp))
	request.Header.Set(SignatureHeader, signature)

	response, err := newHTTPClient(options).Do(request)
	if err != nil {
		return errors.New("command result could not be submitted")
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf("command result endpoint returned HTTP %d", response.StatusCode)
	}
	var acknowledgement commandResultResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&acknowledgement); err != nil ||
		acknowledgement.CommandID != result.CommandID ||
		(acknowledgement.Status != "accepted" && acknowledgement.Status != "duplicate") {
		return errors.New("command result endpoint returned an invalid acknowledgement")
	}
	return nil
}

func SubmitCommandStatus(
	ctx context.Context,
	options Options,
	update contracts.AgentCommandStatusUpdate,
) error {
	if err := validateOptions(options); err != nil {
		return errors.New("command status submission configuration is invalid")
	}
	body, err := json.Marshal(update)
	if err != nil {
		return errors.New("command status could not be encoded")
	}
	timestamp := time.Now().Unix()
	signature, err := signBody(options.PrivateKey, options.AgentID, timestamp, body)
	if err != nil {
		return errors.New("agent signing key is invalid")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, options.Endpoint, bytes.NewReader(body))
	if err != nil {
		return errors.New("command status request could not be created")
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set(AgentHeader, options.AgentID)
	request.Header.Set(TimestampHeader, fmt.Sprintf("%d", timestamp))
	request.Header.Set(SignatureHeader, signature)

	response, err := newHTTPClient(options).Do(request)
	if err != nil {
		return errors.New("command status could not be submitted")
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf("command status endpoint returned HTTP %d", response.StatusCode)
	}
	var acknowledgement commandResultResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&acknowledgement); err != nil ||
		acknowledgement.CommandID != update.CommandID ||
		(acknowledgement.Status != "accepted" && acknowledgement.Status != "duplicate") {
		return errors.New("command status endpoint returned an invalid acknowledgement")
	}
	return nil
}
