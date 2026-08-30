from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netbox_ssot_contracts import (
    CURRENT_AGENT_PROTOCOL_VERSION,
    AgentAssignment,
    AgentCommand,
    AgentCommandResult,
    AgentCommandStatusUpdate,
    AgentConfigurationRequest,
    AgentConfigurationResponse,
    AgentEnrollmentRequest,
    AgentEnrollmentResponse,
    AgentKeyRotationRequest,
    AgentKeyRotationResponse,
    AgentProviderCapability,
)


def test_agent_configuration_contract_round_trip() -> None:
    agent_id = uuid4()
    source_id = uuid4()
    response = AgentConfigurationResponse(
        protocol_version=CURRENT_AGENT_PROTOCOL_VERSION,
        generated_at=datetime.now(UTC),
        agent_id=agent_id,
        agent_name="edge-1",
        ingest_endpoint="https://netbox.example.com/api/plugins/ssot/ingest/batches/",
        command_result_endpoint="https://netbox.example.com/api/plugins/ssot/agent/commands/results/",
        control_interval_seconds=5,
        command_status_endpoint="https://netbox.example.com/api/plugins/ssot/agent/commands/status/",
        assignments=(
            AgentAssignment(
                source_id=source_id,
                source_name="production",
                provider_id="netbox",
                datasets=("regions", "sites"),
                configuration={"base_url": "https://source.example.com", "token_ref": "env://NETBOX_TOKEN"},
                interval_seconds=3_600,
                revision="2026-08-28T12:00:00+00:00",
            ),
        ),
    )

    restored = AgentConfigurationResponse.model_validate_json(response.model_dump_json())

    assert restored.agent_id == agent_id
    assert restored.assignments[0].source_id == source_id
    assert restored.assignments[0].configuration["token_ref"] == "env://NETBOX_TOKEN"
    assert restored.control_interval_seconds == 5


def test_agent_assignment_requires_a_reason_when_schedule_is_paused() -> None:
    assignment = AgentAssignment(
        source_id=uuid4(),
        source_name="production",
        provider_id="netbox",
        datasets=("regions",),
        configuration={},
        interval_seconds=3_600,
        revision="revision",
        schedule_enabled=False,
        schedule_pause_reason="Waiting for review.",
    )

    assert not assignment.schedule_enabled
    with pytest.raises(ValidationError, match="schedule_pause_reason is required"):
        AgentAssignment(
            source_id=uuid4(),
            source_name="production",
            provider_id="netbox",
            datasets=("regions",),
            configuration={},
            interval_seconds=3_600,
            revision="revision",
            schedule_enabled=False,
        )


def test_agent_enrollment_and_rotation_contracts_round_trip() -> None:
    now = datetime.now(UTC)
    enrollment_request = AgentEnrollmentRequest(
        token="nbxssot_" + "a" * 32,
        public_key="A" * 43,
        agent_version="0.6.3-alpha.0",
        protocol_version="1.1",
        providers=(
            AgentProviderCapability(
                provider_id="netbox",
                implementation_version="0.0.2",
                contract_version="1.0",
            ),
        ),
    )
    enrollment_response = AgentEnrollmentResponse(
        agent_id=uuid4(),
        agent_name="edge-1",
        control_endpoint="https://netbox.example.com/api/plugins/ssot/agent/config/",
        key_fingerprint="a" * 64,
    )
    rotation_request = AgentKeyRotationRequest(public_key="B" * 43, agent_version="0.6.3-alpha.0")
    rotation_response = AgentKeyRotationResponse(
        status="rotated",
        key_id=uuid4(),
        key_fingerprint="b" * 64,
        retire_previous_after=now,
    )

    assert AgentEnrollmentRequest.model_validate_json(enrollment_request.model_dump_json()) == enrollment_request
    assert enrollment_request.providers[0].provider_id == "netbox"
    assert AgentEnrollmentResponse.model_validate_json(enrollment_response.model_dump_json()) == enrollment_response
    assert AgentKeyRotationRequest.model_validate_json(rotation_request.model_dump_json()) == rotation_request
    assert AgentKeyRotationResponse.model_validate_json(rotation_response.model_dump_json()) == rotation_response


def test_agent_rotation_contract_rejects_naive_retirement_time() -> None:
    with pytest.raises(ValidationError):
        AgentKeyRotationResponse(
            status="rotated",
            key_id=uuid4(),
            key_fingerprint="a" * 64,
            retire_previous_after=datetime.now(),
        )


def test_agent_command_contract_round_trip() -> None:
    source_id = uuid4()
    command_id = uuid4()
    assignment = AgentAssignment(
        source_id=source_id,
        source_name="production",
        provider_id="netbox",
        datasets=("sites",),
        configuration={"token_ref": "env://NETBOX_TOKEN"},
        interval_seconds=3_600,
        revision="revision",
    )
    command = AgentCommand(
        command_id=command_id,
        kind="run_now",
        requested_at=datetime.now(UTC),
        assignment=assignment,
    )
    result = AgentCommandResult(
        command_id=command_id,
        source_id=source_id,
        kind="run_now",
        succeeded=True,
        summary="Collection completed and was accepted by NetBox.",
        run_id=command_id,
        collection_state="complete",
        submission_state="accepted",
        completed_at=datetime.now(UTC),
        duration_seconds=1.25,
    )
    status = AgentCommandStatusUpdate(
        command_id=command_id,
        source_id=source_id,
        kind="run_now",
        state="running",
        summary="Agent started executing the command.",
        occurred_at=datetime.now(UTC),
    )

    assert AgentCommand.model_validate_json(command.model_dump_json()) == command
    assert AgentCommandResult.model_validate_json(result.model_dump_json()) == result
    assert AgentCommandStatusUpdate.model_validate_json(status.model_dump_json()) == status


def test_protocol_11_requires_command_result_endpoint() -> None:
    with pytest.raises(ValidationError):
        AgentConfigurationResponse(
            protocol_version="1.1",
            generated_at=datetime.now(UTC),
            agent_id=uuid4(),
            agent_name="edge-1",
            ingest_endpoint="https://netbox.example.com/api/plugins/ssot/ingest/batches/",
        )


def test_protocol_10_remains_compatible_without_commands() -> None:
    response = AgentConfigurationResponse(
        protocol_version="1.0",
        generated_at=datetime.now(UTC),
        agent_id=uuid4(),
        agent_name="edge-1",
        ingest_endpoint="https://netbox.example.com/api/plugins/ssot/ingest/batches/",
    )

    assert response.commands == ()


def test_agent_configuration_request_rejects_unknown_protocol() -> None:
    with pytest.raises(ValidationError):
        AgentConfigurationRequest(protocol_version="2.0", agent_version="0.4.0")  # type: ignore[arg-type]


def test_agent_configuration_request_reports_active_sources() -> None:
    source_id = uuid4()
    request = AgentConfigurationRequest(
        protocol_version="1.1",
        agent_version="0.6.8-alpha.0",
        active_source_ids=(source_id,),
    )

    restored = AgentConfigurationRequest.model_validate_json(request.model_dump_json())

    assert restored.active_source_ids == (source_id,)


@pytest.mark.parametrize("interval", [0, 1, 31])
def test_agent_configuration_request_rejects_unsafe_control_intervals(interval: int) -> None:
    with pytest.raises(ValidationError):
        AgentConfigurationRequest(
            protocol_version="1.1",
            agent_version="0.6.0",
            control_interval_seconds=interval,
        )


@pytest.mark.parametrize("interval", [0, 59, 2_592_001])
def test_agent_assignment_rejects_unsafe_intervals(interval: int) -> None:
    with pytest.raises(ValidationError):
        AgentAssignment(
            source_id=uuid4(),
            source_name="production",
            provider_id="netbox",
            datasets=("sites",),
            configuration={},
            interval_seconds=interval,
            revision="revision",
        )
