package submission

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	contracts "github.com/abhi1693/netbox-ssot/internal/contracts"
)

func FetchConfiguration(
	ctx context.Context,
	options Options,
	agentVersion string,
	controlIntervalSeconds int,
	activeCommandIDs []string,
	providers []contracts.AgentProviderCapability,
) (contracts.AgentConfigurationResponse, error) {
	if err := validateOptions(options); err != nil || controlIntervalSeconds < 2 || controlIntervalSeconds > 30 {
		return contracts.AgentConfigurationResponse{}, errors.New("agent control configuration is invalid")
	}
	body, err := json.Marshal(contracts.AgentConfigurationRequest{
		ProtocolVersion:        contracts.AgentProtocolVersion,
		AgentVersion:           agentVersion,
		ControlIntervalSeconds: controlIntervalSeconds,
		ActiveCommandIDs:       activeCommandIDs,
		Providers:              providers,
	})
	if err != nil {
		return contracts.AgentConfigurationResponse{}, errors.New("agent configuration request could not be encoded")
	}
	timestamp := time.Now().Unix()
	signature, err := signBody(options.PrivateKey, options.AgentID, timestamp, body)
	if err != nil {
		return contracts.AgentConfigurationResponse{}, errors.New("agent signing key is invalid")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, options.Endpoint, bytes.NewReader(body))
	if err != nil {
		return contracts.AgentConfigurationResponse{}, errors.New("agent configuration request could not be created")
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "netbox-ssot-agent/"+agentVersion)
	request.Header.Set(AgentHeader, options.AgentID)
	request.Header.Set(TimestampHeader, fmt.Sprintf("%d", timestamp))
	request.Header.Set(SignatureHeader, signature)

	response, err := newHTTPClient(options).Do(request)
	if err != nil {
		return contracts.AgentConfigurationResponse{}, errors.New("agent configuration could not be fetched")
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return contracts.AgentConfigurationResponse{}, fmt.Errorf(
			"agent configuration endpoint returned HTTP %d",
			response.StatusCode,
		)
	}

	var configuration contracts.AgentConfigurationResponse
	decoder := json.NewDecoder(io.LimitReader(response.Body, maxResponseSize))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&configuration); err != nil {
		return contracts.AgentConfigurationResponse{}, errors.New("agent configuration response is invalid")
	}
	if configuration.ProtocolVersion != contracts.AgentProtocolVersion ||
		configuration.AgentID != options.AgentID ||
		configuration.IngestEndpoint == "" ||
		!sameOrigin(options.Endpoint, configuration.IngestEndpoint) ||
		configuration.CommandResultEndpoint == "" ||
		!sameOrigin(options.Endpoint, configuration.CommandResultEndpoint) ||
		(configuration.CommandStatusEndpoint != "" &&
			!sameOrigin(options.Endpoint, configuration.CommandStatusEndpoint)) ||
		(configuration.ControlIntervalSeconds != 0 &&
			(configuration.ControlIntervalSeconds < 2 || configuration.ControlIntervalSeconds > 30)) ||
		!validAssignments(configuration.Assignments) ||
		!validCommands(configuration.Commands, configuration.Assignments) {
		return contracts.AgentConfigurationResponse{}, errors.New("agent configuration response is incompatible")
	}
	return configuration, nil
}

func validCommands(commands []contracts.AgentCommand, assignments []contracts.AgentAssignment) bool {
	assignmentIDs := make(map[string]bool, len(assignments))
	for _, assignment := range assignments {
		assignmentIDs[assignment.SourceID] = true
	}
	seen := make(map[string]bool, len(commands))
	for _, command := range commands {
		if command.CommandID == "" || seen[command.CommandID] || !assignmentIDs[command.Assignment.SourceID] ||
			(command.Kind != "test_connection" && command.Kind != "run_now") ||
			!validAssignments([]contracts.AgentAssignment{command.Assignment}) {
			return false
		}
		seen[command.CommandID] = true
	}
	return true
}

func sameOrigin(controlEndpoint string, ingestEndpoint string) bool {
	controlURL, controlErr := url.Parse(controlEndpoint)
	ingestURL, ingestErr := url.Parse(ingestEndpoint)
	return controlErr == nil && ingestErr == nil &&
		controlURL.Scheme == ingestURL.Scheme && controlURL.Host == ingestURL.Host
}

func validAssignments(assignments []contracts.AgentAssignment) bool {
	seen := make(map[string]bool, len(assignments))
	for _, assignment := range assignments {
		scheduleEnabled := assignmentScheduleEnabled(assignment)
		if assignment.SourceID == "" || assignment.SourceName == "" || assignment.ProviderID == "" ||
			assignment.ExecutionMode != "agent" || len(assignment.Datasets) == 0 ||
			assignment.IntervalSeconds < 60 || assignment.IntervalSeconds > 2_592_000 ||
			assignment.Revision == "" || seen[assignment.SourceID] ||
			(!scheduleEnabled && assignment.SchedulePauseReason == "") ||
			(scheduleEnabled && assignment.SchedulePauseReason != "") {
			return false
		}
		seen[assignment.SourceID] = true
	}
	return true
}

func assignmentScheduleEnabled(assignment contracts.AgentAssignment) bool {
	return assignment.ScheduleEnabled == nil || *assignment.ScheduleEnabled
}
