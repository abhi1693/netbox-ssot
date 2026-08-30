from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from .base import ContractModel, Identifier
from .observation import CollectionMessage, ScopeDimension

CURRENT_AGENT_PROTOCOL_VERSION = "1.1"
AgentProtocolVersion = Literal["1.0", "1.1"]
AgentCommandKind = Literal["test_connection", "run_now"]
AgentCommandProgressState = Literal["running", "reporting"]


class AgentProviderCapability(ContractModel):
    provider_id: Identifier
    implementation_version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
    contract_version: str = Field(pattern=r"^[1-9]\d*\.\d+$")


class AgentEnrollmentRequest(ContractModel):
    token: str = Field(min_length=32, max_length=128)
    public_key: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    agent_version: str = Field(min_length=1, max_length=64)
    protocol_version: AgentProtocolVersion
    providers: tuple[AgentProviderCapability, ...] = Field(default=(), max_length=100)


class AgentEnrollmentResponse(ContractModel):
    status: Literal["enrolled"] = "enrolled"
    agent_id: UUID
    agent_name: str = Field(min_length=1, max_length=100)
    control_endpoint: AnyHttpUrl
    key_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class AgentKeyRotationRequest(ContractModel):
    public_key: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    agent_version: str = Field(min_length=1, max_length=64)


class AgentKeyRotationResponse(ContractModel):
    status: Literal["rotated", "duplicate"]
    key_id: UUID
    key_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    retire_previous_after: datetime

    @field_validator("retire_previous_after")
    @classmethod
    def require_retirement_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retire_previous_after must include a timezone")
        return value


class AgentConfigurationRequest(ContractModel):
    protocol_version: AgentProtocolVersion
    agent_version: str = Field(min_length=1, max_length=64)
    control_interval_seconds: int = Field(default=30, ge=2, le=30)
    active_command_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    active_source_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    providers: tuple[AgentProviderCapability, ...] = Field(default=(), max_length=100)


class AgentAssignment(ContractModel):
    source_id: UUID
    source_name: str = Field(min_length=1, max_length=100)
    provider_id: Identifier
    execution_mode: Literal["agent"] = "agent"
    datasets: tuple[Identifier, ...] = Field(min_length=1)
    scope: tuple[ScopeDimension, ...] = ()
    configuration: dict[str, Any]
    interval_seconds: int = Field(ge=60, le=2_592_000)
    revision: str = Field(min_length=1, max_length=64)
    schedule_enabled: bool = True
    schedule_pause_reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def require_schedule_pause_reason(self) -> AgentAssignment:
        if not self.schedule_enabled and not self.schedule_pause_reason:
            raise ValueError("schedule_pause_reason is required when scheduled collection is paused")
        if self.schedule_enabled and self.schedule_pause_reason:
            raise ValueError("schedule_pause_reason is only valid when scheduled collection is paused")
        return self


class AgentCommand(ContractModel):
    command_id: UUID
    kind: AgentCommandKind
    requested_at: datetime
    assignment: AgentAssignment

    @field_validator("requested_at")
    @classmethod
    def require_requested_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requested_at must include a timezone")
        return value


class AgentConfigurationResponse(ContractModel):
    protocol_version: AgentProtocolVersion
    generated_at: datetime
    agent_id: UUID
    agent_name: str = Field(min_length=1, max_length=100)
    ingest_endpoint: AnyHttpUrl
    assignments: tuple[AgentAssignment, ...] = ()
    command_result_endpoint: AnyHttpUrl | None = None
    commands: tuple[AgentCommand, ...] = ()
    control_interval_seconds: int | None = Field(default=None, ge=2, le=30)
    command_status_endpoint: AnyHttpUrl | None = None

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone")
        return value

    @model_validator(mode="after")
    def require_protocol_features(self) -> AgentConfigurationResponse:
        if self.protocol_version == "1.1" and self.command_result_endpoint is None:
            raise ValueError("command_result_endpoint is required for protocol 1.1")
        if self.protocol_version == "1.0" and (
            self.command_result_endpoint is not None
            or self.commands
            or self.control_interval_seconds is not None
            or self.command_status_endpoint is not None
        ):
            raise ValueError("protocol 1.0 does not support commands")
        return self


class AgentCommandResult(ContractModel):
    command_id: UUID
    source_id: UUID
    kind: AgentCommandKind
    succeeded: bool
    summary: str = Field(min_length=1, max_length=500)
    run_id: UUID | None = None
    collection_state: str = Field(default="", max_length=32)
    submission_state: str = Field(default="", max_length=32)
    details: tuple[CollectionMessage, ...] = ()
    completed_at: datetime
    duration_seconds: float | None = Field(default=None, ge=0)

    @field_validator("completed_at")
    @classmethod
    def require_completed_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completed_at must include a timezone")
        return value


class AgentCommandStatusUpdate(ContractModel):
    command_id: UUID
    source_id: UUID
    kind: AgentCommandKind
    state: AgentCommandProgressState
    summary: str = Field(min_length=1, max_length=500)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def require_occurred_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value
