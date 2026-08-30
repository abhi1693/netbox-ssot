package contracts

import (
	"encoding/json"
	"time"
)

const (
	ContractVersion      = "1.0"
	AgentProtocolVersion = "1.1"
)

type AgentCompatibility struct {
	ProtocolVersion     string `json:"protocol_version"`
	MinimumAgentVersion string `json:"minimum_agent_version"`
	CollectorID         string `json:"collector_id"`
}

type DatasetDefinition struct {
	ID             string             `json:"id"`
	Title          string             `json:"title"`
	Description    string             `json:"description"`
	ResourceKinds  []string           `json:"resource_kinds"`
	DefaultEnabled bool               `json:"default_enabled"`
	Selectable     *bool              `json:"selectable,omitempty"`
	Completeness   string             `json:"completeness"`
	DependsOn      []string           `json:"depends_on"`
	DataMappings   []DataModelMapping `json:"data_mappings"`
}

type DataModelMapping struct {
	SourceName      string `json:"source_name"`
	SourceModel     string `json:"source_model"`
	SourcePath      string `json:"source_path,omitempty"`
	DestinationKind string `json:"destination_kind"`
}

type ProviderManifest struct {
	ProviderID            string              `json:"provider_id"`
	DisplayName           string              `json:"display_name"`
	IconClass             string              `json:"icon_class"`
	Description           string              `json:"description"`
	InstanceURLField      string              `json:"instance_url_field,omitempty"`
	ImplementationVersion string              `json:"implementation_version"`
	ContractVersion       string              `json:"contract_version"`
	DocumentationURL      string              `json:"documentation_url"`
	ExecutionModes        []string            `json:"execution_modes"`
	Capabilities          []string            `json:"capabilities"`
	AgentCompatibility    AgentCompatibility  `json:"agent_compatibility"`
	ConfigSchema          json.RawMessage     `json:"config_schema"`
	SecretFields          []string            `json:"secret_fields"`
	Datasets              []DatasetDefinition `json:"datasets"`
}

type ScopeDimension struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

type CollectionRequest struct {
	RunID         string           `json:"run_id"`
	SourceID      string           `json:"source_id"`
	ProviderID    string           `json:"provider_id"`
	ExecutionMode string           `json:"execution_mode"`
	Datasets      []string         `json:"datasets"`
	Scope         []ScopeDimension `json:"scope"`
	Configuration map[string]any   `json:"configuration"`
}

type ConnectionTestRequest struct {
	SourceID      string         `json:"source_id"`
	ProviderID    string         `json:"provider_id"`
	ExecutionMode string         `json:"execution_mode"`
	Configuration map[string]any `json:"configuration"`
}

type CollectionMessage struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
}

type ConnectionTestResult struct {
	Succeeded bool                `json:"succeeded"`
	Summary   string              `json:"summary"`
	Details   []CollectionMessage `json:"details"`
}

type AgentConfigurationRequest struct {
	ProtocolVersion        string                    `json:"protocol_version"`
	AgentVersion           string                    `json:"agent_version"`
	ControlIntervalSeconds int                       `json:"control_interval_seconds"`
	ActiveCommandIDs       []string                  `json:"active_command_ids"`
	ActiveSourceIDs        []string                  `json:"active_source_ids"`
	Providers              []AgentProviderCapability `json:"providers"`
}

type AgentProviderCapability struct {
	ProviderID            string `json:"provider_id"`
	ImplementationVersion string `json:"implementation_version"`
	ContractVersion       string `json:"contract_version"`
}

type AgentEnrollmentRequest struct {
	Token           string                    `json:"token"`
	PublicKey       string                    `json:"public_key"`
	AgentVersion    string                    `json:"agent_version"`
	ProtocolVersion string                    `json:"protocol_version"`
	Providers       []AgentProviderCapability `json:"providers"`
}

type AgentEnrollmentResponse struct {
	Status          string `json:"status"`
	AgentID         string `json:"agent_id"`
	AgentName       string `json:"agent_name"`
	ControlEndpoint string `json:"control_endpoint"`
	KeyFingerprint  string `json:"key_fingerprint"`
}

type AgentKeyRotationRequest struct {
	PublicKey    string `json:"public_key"`
	AgentVersion string `json:"agent_version"`
}

type AgentKeyRotationResponse struct {
	Status              string    `json:"status"`
	KeyID               string    `json:"key_id"`
	KeyFingerprint      string    `json:"key_fingerprint"`
	RetirePreviousAfter time.Time `json:"retire_previous_after"`
}

type AgentAssignment struct {
	SourceID            string           `json:"source_id"`
	SourceName          string           `json:"source_name"`
	ProviderID          string           `json:"provider_id"`
	ExecutionMode       string           `json:"execution_mode"`
	Datasets            []string         `json:"datasets"`
	Scope               []ScopeDimension `json:"scope"`
	Configuration       map[string]any   `json:"configuration"`
	IntervalSeconds     int              `json:"interval_seconds"`
	Revision            string           `json:"revision"`
	ScheduleEnabled     *bool            `json:"schedule_enabled,omitempty"`
	SchedulePauseReason string           `json:"schedule_pause_reason,omitempty"`
}

type AgentConfigurationResponse struct {
	ProtocolVersion        string            `json:"protocol_version"`
	GeneratedAt            time.Time         `json:"generated_at"`
	AgentID                string            `json:"agent_id"`
	AgentName              string            `json:"agent_name"`
	IngestEndpoint         string            `json:"ingest_endpoint"`
	Assignments            []AgentAssignment `json:"assignments"`
	CommandResultEndpoint  string            `json:"command_result_endpoint,omitempty"`
	Commands               []AgentCommand    `json:"commands,omitempty"`
	ControlIntervalSeconds int               `json:"control_interval_seconds,omitempty"`
	CommandStatusEndpoint  string            `json:"command_status_endpoint,omitempty"`
}

type AgentCommand struct {
	CommandID   string          `json:"command_id"`
	Kind        string          `json:"kind"`
	RequestedAt time.Time       `json:"requested_at"`
	Assignment  AgentAssignment `json:"assignment"`
}

type AgentCommandResult struct {
	CommandID       string              `json:"command_id"`
	SourceID        string              `json:"source_id"`
	Kind            string              `json:"kind"`
	Succeeded       bool                `json:"succeeded"`
	Summary         string              `json:"summary"`
	RunID           string              `json:"run_id,omitempty"`
	CollectionState string              `json:"collection_state,omitempty"`
	SubmissionState string              `json:"submission_state,omitempty"`
	Details         []CollectionMessage `json:"details"`
	CompletedAt     time.Time           `json:"completed_at"`
	DurationSeconds float64             `json:"duration_seconds,omitempty"`
}

type AgentCommandStatusUpdate struct {
	CommandID  string    `json:"command_id"`
	SourceID   string    `json:"source_id"`
	Kind       string    `json:"kind"`
	State      string    `json:"state"`
	Summary    string    `json:"summary"`
	OccurredAt time.Time `json:"occurred_at"`
}

type ObservationAttribute struct {
	Path  string `json:"path"`
	Value any    `json:"value"`
}

type Relationship struct {
	Kind             string `json:"kind"`
	TargetKind       string `json:"target_kind"`
	TargetExternalID string `json:"target_external_id"`
}

type Evidence struct {
	SourceObjectType string    `json:"source_object_type"`
	SourceObjectID   string    `json:"source_object_id"`
	AttributePaths   []string  `json:"attribute_paths"`
	RawDigest        string    `json:"raw_digest,omitempty"`
	Note             string    `json:"note"`
	ObservedAt       time.Time `json:"observed_at"`
}

type Observation struct {
	ResourceKind  string                 `json:"resource_kind"`
	ExternalID    string                 `json:"external_id"`
	SourceID      string                 `json:"source_id"`
	ProviderID    string                 `json:"provider_id"`
	Scope         []ScopeDimension       `json:"scope"`
	CollectedAt   time.Time              `json:"collected_at"`
	Attributes    []ObservationAttribute `json:"attributes"`
	Relationships []Relationship         `json:"relationships"`
	Evidence      []Evidence             `json:"evidence"`
}

type ObservationBatch struct {
	RunID             string              `json:"run_id"`
	SourceID          string              `json:"source_id"`
	ProviderID        string              `json:"provider_id"`
	ProviderVersion   string              `json:"provider_version"`
	ContractVersion   string              `json:"contract_version"`
	State             string              `json:"state"`
	StartedAt         time.Time           `json:"started_at"`
	CompletedAt       time.Time           `json:"completed_at"`
	Datasets          []string            `json:"datasets"`
	Scope             []ScopeDimension    `json:"scope"`
	Observations      []Observation       `json:"observations"`
	Messages          []CollectionMessage `json:"messages"`
	CompletenessToken string              `json:"completeness_token,omitempty"`
}

type SubmissionResult struct {
	Succeeded        bool                `json:"succeeded"`
	Status           string              `json:"status,omitempty"`
	Summary          string              `json:"summary"`
	RunID            string              `json:"run_id,omitempty"`
	ObservationCount int                 `json:"observation_count,omitempty"`
	PayloadDigest    string              `json:"payload_digest,omitempty"`
	Details          []CollectionMessage `json:"details"`
}

type SourceSyncResult struct {
	Succeeded       bool                `json:"succeeded"`
	SourceID        string              `json:"source_id"`
	SourceName      string              `json:"source_name"`
	RunID           string              `json:"run_id,omitempty"`
	CollectionState string              `json:"collection_state,omitempty"`
	SubmissionState string              `json:"submission_state,omitempty"`
	Summary         string              `json:"summary"`
	Details         []CollectionMessage `json:"details"`
}

type AgentSyncResult struct {
	Succeeded       bool                `json:"succeeded"`
	Summary         string              `json:"summary"`
	AssignmentCount int                 `json:"assignment_count"`
	CompletedCount  int                 `json:"completed_count"`
	FailedCount     int                 `json:"failed_count"`
	Results         []SourceSyncResult  `json:"results"`
	Details         []CollectionMessage `json:"details"`
}
