from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from netbox.plugins import get_plugin_config
from pydantic import ValidationError
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from netbox_ssot_contracts import (
    CURRENT_AGENT_PROTOCOL_VERSION,
    AgentAssignment,
    AgentCommandResult,
    AgentCommandStatusUpdate,
    AgentConfigurationRequest,
    AgentConfigurationResponse,
    AgentEnrollmentRequest,
    AgentEnrollmentResponse,
    AgentKeyRotationRequest,
    AgentKeyRotationResponse,
    ObservationBatch,
)
from netbox_ssot_contracts import (
    AgentCommand as AgentCommandContract,
)

from ..agent_capabilities import serialized_capabilities, source_capability_issue
from ..agent_security import AgentSecurityError, enroll_agent, rotate_agent_key
from ..collection_policy import (
    agent_collection_policy_issue,
    source_collection_schedule_policy,
)
from ..ingestion.service import IngestionConflictError, IngestionRejectedError, ingest_batch
from ..models import AgentCommand, AgentSecurityEvent, CollectionRun, CollectorAgent, DiscoverySource
from ..preparation import request_comparison_preparation
from .authentication import AgentSignatureAuthentication


class AgentEnrollmentAPIView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request: object) -> Response:
        try:
            enrollment_request = AgentEnrollmentRequest.model_validate_json(request.body)
        except ValidationError:
            return Response(
                {"code": "invalid_request", "message": "Enrollment request is invalid or unavailable."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            agent, signing_key = enroll_agent(
                token=enrollment_request.token,
                public_key=enrollment_request.public_key,
                agent_version=enrollment_request.agent_version,
                protocol_version=enrollment_request.protocol_version,
                providers=enrollment_request.providers,
            )
        except AgentSecurityError:
            return Response(
                {"code": "enrollment_unavailable", "message": "Enrollment request is invalid or unavailable."},
                status=status.HTTP_403_FORBIDDEN,
            )
        response = AgentEnrollmentResponse(
            agent_id=agent.id,
            agent_name=agent.name,
            control_endpoint=request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:agent-config")),
            key_fingerprint=signing_key.fingerprint,
        )
        return Response(response.model_dump(mode="json"), status=status.HTTP_201_CREATED)


class AgentKeyRotationAPIView(APIView):
    authentication_classes = (AgentSignatureAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request: object) -> Response:
        try:
            rotation_request = AgentKeyRotationRequest.model_validate_json(request.body)
        except ValidationError:
            return Response(
                {"code": "invalid_request", "message": "Key rotation request is invalid."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        agent = request.auth
        if not isinstance(agent, CollectorAgent):
            return Response(
                {"code": "authentication_failed", "message": "Agent authentication failed."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        grace_seconds = int(get_plugin_config("netbox_ssot", "agent_key_rotation_grace_seconds"))
        try:
            signing_key, retire_after, duplicate = rotate_agent_key(
                agent=agent,
                public_key=rotation_request.public_key,
                grace_period=timedelta(seconds=grace_seconds),
            )
        except AgentSecurityError as exc:
            return Response(
                {"code": "rotation_rejected", "message": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        CollectorAgent.objects.filter(pk=agent.pk).update(agent_version=rotation_request.agent_version)
        response = AgentKeyRotationResponse(
            status="duplicate" if duplicate else "rotated",
            key_id=signing_key.id,
            key_fingerprint=signing_key.fingerprint,
            retire_previous_after=retire_after,
        )
        return Response(response.model_dump(mode="json"))


class AgentConfigurationView(APIView):
    authentication_classes = (AgentSignatureAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request: object) -> Response:
        try:
            configuration_request = AgentConfigurationRequest.model_validate_json(request.body)
        except ValidationError:
            return Response(
                {"code": "invalid_request", "message": "Request body does not conform to the agent protocol."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agent = request.auth
        if not isinstance(agent, CollectorAgent):
            return Response(
                {"code": "authentication_failed", "message": "Agent authentication failed."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        capabilities_in_request = "providers" in configuration_request.model_fields_set
        previous_capabilities = agent.provider_capabilities
        reported_capabilities = (
            serialized_capabilities(configuration_request.providers)
            if capabilities_in_request
            else agent.provider_capabilities
        )
        agent.agent_version = configuration_request.agent_version
        agent.protocol_version = configuration_request.protocol_version
        agent.provider_capabilities = reported_capabilities
        assigned_sources = tuple(agent.sources.filter(enabled=True).order_by("name"))
        policy_issue = agent_collection_policy_issue(agent)
        sources = tuple(
            source
            for source in assigned_sources
            if not policy_issue and not source_capability_issue(agent, source.provider_id)
        )
        assignments = tuple(_agent_assignment(source) for source in sources)
        now = timezone.now()
        agent_updates = {
            "last_seen_at": now,
            "agent_version": configuration_request.agent_version,
            "protocol_version": configuration_request.protocol_version,
            "reported_control_interval_seconds": configuration_request.control_interval_seconds,
        }
        if capabilities_in_request:
            agent_updates.update(
                provider_capabilities=reported_capabilities,
                capabilities_reported_at=now,
            )
        CollectorAgent.objects.filter(pk=agent.pk).update(
            **agent_updates,
        )
        if "active_source_ids" in configuration_request.model_fields_set:
            active_source_ids = set(configuration_request.active_source_ids)
            assigned_source_ids = set(agent.sources.values_list("pk", flat=True))
            unknown_source_ids = active_source_ids - assigned_source_ids
            if unknown_source_ids:
                return Response(
                    {
                        "code": "invalid_active_source",
                        "message": "Active collection status may only be reported for sources assigned to this agent.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            inactive_sources = agent.sources.exclude(pk__in=active_source_ids)
            inactive_sources.update(active_collection_started_at=None, active_collection_seen_at=None)
            active_sources = agent.sources.filter(pk__in=active_source_ids)
            active_sources.filter(active_collection_started_at__isnull=True).update(active_collection_started_at=now)
            active_sources.update(active_collection_seen_at=now)
        if capabilities_in_request and reported_capabilities != previous_capabilities:
            incompatible_sources = [
                str(source.id) for source in assigned_sources if source_capability_issue(agent, source.provider_id)
            ]
            AgentSecurityEvent.objects.create(
                agent=agent,
                kind=AgentSecurityEvent.Kind.CAPABILITIES_UPDATED,
                details={
                    "previous": previous_capabilities,
                    "current": reported_capabilities,
                    "incompatible_source_ids": incompatible_sources,
                },
            )
        AgentCommand.objects.filter(
            pk__in=configuration_request.active_command_ids,
            agent=agent,
            state__in=(
                AgentCommand.State.DISPATCHED,
                AgentCommand.State.RUNNING,
                AgentCommand.State.REPORTING,
            ),
        ).update(last_progress_at=now)
        if configuration_request.protocol_version == "1.0":
            return Response(
                {
                    "protocol_version": "1.0",
                    "generated_at": now.isoformat(),
                    "agent_id": str(agent.id),
                    "agent_name": agent.name,
                    "ingest_endpoint": request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:batch-ingest")),
                    "assignments": [
                        _serialized_assignment(assignment, include_schedule_policy=False) for assignment in assignments
                    ],
                }
            )

        commands = () if policy_issue else _claim_commands(agent, now, sources)
        response = AgentConfigurationResponse(
            protocol_version=CURRENT_AGENT_PROTOCOL_VERSION,
            generated_at=now,
            agent_id=agent.id,
            agent_name=agent.name,
            ingest_endpoint=request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:batch-ingest")),
            assignments=assignments,
            command_result_endpoint=request.build_absolute_uri(
                reverse("plugins-api:netbox_ssot-api:agent-command-result")
            ),
            commands=commands,
            control_interval_seconds=(
                agent.control_interval_seconds
                if _supports_server_managed_interval(configuration_request.agent_version)
                else None
            ),
            command_status_endpoint=(
                request.build_absolute_uri(reverse("plugins-api:netbox_ssot-api:agent-command-status"))
                if _supports_command_progress(configuration_request.agent_version)
                else None
            ),
        )
        return Response(
            _serialized_configuration_response(
                response,
                include_schedule_policy=_supports_collection_pause(configuration_request.agent_version),
            )
        )


class AgentCommandResultView(APIView):
    authentication_classes = (AgentSignatureAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request: object) -> Response:
        try:
            result = AgentCommandResult.model_validate_json(request.body)
        except ValidationError:
            return Response(
                {"code": "invalid_request", "message": "Request body does not conform to the agent protocol."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agent = request.auth
        if not isinstance(agent, CollectorAgent):
            return Response(
                {"code": "authentication_failed", "message": "Agent authentication failed."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            command = AgentCommand.objects.select_related("source").get(pk=result.command_id, agent=agent)
        except AgentCommand.DoesNotExist:
            return Response(
                {"code": "command_not_found", "message": "Command is not assigned to this agent."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if str(command.source_id) != str(result.source_id) or command.kind != result.kind:
            return Response(
                {"code": "command_mismatch", "message": "Command result does not match the dispatched command."},
                status=status.HTTP_409_CONFLICT,
            )
        if command.state in {AgentCommand.State.SUCCEEDED, AgentCommand.State.FAILED}:
            return Response({"status": "duplicate", "command_id": str(command.id)})

        command.state = AgentCommand.State.SUCCEEDED if result.succeeded else AgentCommand.State.FAILED
        command.completed_at = result.completed_at
        command.last_progress_at = timezone.now()
        command.result = result.model_dump(mode="json")
        command.save(update_fields=("state", "completed_at", "last_progress_at", "result"))
        CollectorAgent.objects.filter(pk=agent.pk).update(last_seen_at=timezone.now())
        return Response({"status": "accepted", "command_id": str(command.id)})


class AgentCommandStatusView(APIView):
    authentication_classes = (AgentSignatureAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request: object) -> Response:
        try:
            update = AgentCommandStatusUpdate.model_validate_json(request.body)
        except ValidationError:
            return Response(
                {"code": "invalid_request", "message": "Request body does not conform to the agent protocol."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        agent = request.auth
        if not isinstance(agent, CollectorAgent):
            return Response(
                {"code": "authentication_failed", "message": "Agent authentication failed."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            command = AgentCommand.objects.get(pk=update.command_id, agent=agent)
        except AgentCommand.DoesNotExist:
            return Response(
                {"code": "command_not_found", "message": "Command is not assigned to this agent."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if str(command.source_id) != str(update.source_id) or command.kind != update.kind:
            return Response(
                {"code": "command_mismatch", "message": "Command status does not match the dispatched command."},
                status=status.HTTP_409_CONFLICT,
            )
        if command.state in {AgentCommand.State.SUCCEEDED, AgentCommand.State.FAILED}:
            return Response({"status": "duplicate", "command_id": str(command.id)})
        state_order = {
            AgentCommand.State.PENDING: 0,
            AgentCommand.State.DISPATCHED: 1,
            AgentCommand.State.RUNNING: 2,
            AgentCommand.State.REPORTING: 3,
        }
        if state_order.get(update.state, -1) < state_order.get(command.state, -1):
            return Response({"status": "duplicate", "command_id": str(command.id)})
        command.state = update.state
        command.last_progress_at = timezone.now()
        command.result = {"summary": update.summary, "occurred_at": update.occurred_at.isoformat()}
        update_fields = ["state", "last_progress_at", "result"]
        if update.state == AgentCommand.State.RUNNING and command.started_at is None:
            command.started_at = update.occurred_at
            update_fields.append("started_at")
        if update.state == AgentCommand.State.REPORTING and command.reporting_at is None:
            command.reporting_at = update.occurred_at
            update_fields.append("reporting_at")
        command.save(update_fields=update_fields)
        return Response({"status": "accepted", "command_id": str(command.id)})


class BatchIngestView(APIView):
    authentication_classes = (AgentSignatureAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request: object) -> Response:
        try:
            batch = ObservationBatch.model_validate_json(request.body)
        except ValidationError:
            return Response(
                {"code": "invalid_batch", "message": "Request body does not conform to the observation contract."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agent = request.auth
        if not isinstance(agent, CollectorAgent):
            return Response(
                {"code": "authentication_failed", "message": "Agent authentication failed."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            outcome = ingest_batch(agent=agent, batch=batch)
        except IngestionRejectedError as exc:
            return Response({"code": "batch_rejected", "message": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except IngestionConflictError as exc:
            return Response({"code": "run_conflict", "message": str(exc)}, status=status.HTTP_409_CONFLICT)

        preparation_state = "not_requested"
        run = CollectionRun.objects.get(pk=outcome.run_id)
        if run.state == "complete" and run.completeness_token:
            preparation = request_comparison_preparation(run).preparation
            preparation_state = preparation.state

        response_status = status.HTTP_201_CREATED if outcome.status == "accepted" else status.HTTP_200_OK
        return Response(
            {
                "status": outcome.status,
                "run_id": outcome.run_id,
                "observation_count": outcome.observation_count,
                "payload_digest": outcome.payload_digest,
                "comparison_preparation": preparation_state,
            },
            status=response_status,
        )


def _agent_assignment(source: DiscoverySource) -> AgentAssignment:
    schedule_policy = source_collection_schedule_policy(source)
    return AgentAssignment(
        source_id=source.id,
        source_name=source.name,
        provider_id=source.provider_id,
        datasets=tuple(source.datasets),
        configuration=source.configuration,
        interval_seconds=source.collection_interval_minutes * 60,
        revision=source.updated_at.isoformat(),
        schedule_enabled=schedule_policy.enabled,
        schedule_pause_reason=schedule_policy.reason,
    )


def _supports_server_managed_interval(agent_version: str) -> bool:
    return _agent_version_at_least(agent_version, (0, 0, 1))


def _supports_command_progress(agent_version: str) -> bool:
    return _agent_version_at_least(agent_version, (0, 0, 1))


def _supports_collection_pause(agent_version: str) -> bool:
    return _agent_version_at_least(agent_version, (0, 0, 1))


def _serialized_assignment(assignment: AgentAssignment, *, include_schedule_policy: bool) -> dict[str, object]:
    payload = assignment.model_dump(mode="json")
    if not include_schedule_policy:
        payload.pop("schedule_enabled", None)
        payload.pop("schedule_pause_reason", None)
    return payload


def _serialized_configuration_response(
    response: AgentConfigurationResponse,
    *,
    include_schedule_policy: bool,
) -> dict[str, object]:
    payload = response.model_dump(mode="json", exclude_none=True)
    if include_schedule_policy:
        return payload
    for assignment in payload["assignments"]:
        assignment.pop("schedule_enabled", None)
        assignment.pop("schedule_pause_reason", None)
    for command in payload.get("commands", []):
        command_assignment = command["assignment"]
        command_assignment.pop("schedule_enabled", None)
        command_assignment.pop("schedule_pause_reason", None)
    return payload


def _agent_version_at_least(agent_version: str, required: tuple[int, int, int]) -> bool:
    try:
        parts = agent_version.split("-", 1)[0].split(".")
        version = tuple(int(part) for part in parts)
        return len(version) == 3 and version >= required
    except (TypeError, ValueError):
        return False


def _claim_commands(
    agent: CollectorAgent,
    now: datetime,
    capability_approved_sources: tuple[DiscoverySource, ...],
) -> tuple[AgentCommandContract, ...]:
    lease_cutoff = now - timedelta(minutes=5)
    source_ids = tuple(source.pk for source in capability_approved_sources)
    with transaction.atomic():
        candidates = tuple(
            AgentCommand.objects.select_for_update()
            .select_related("source")
            .filter(
                agent=agent,
                source_id__in=source_ids,
                source__enabled=True,
                source__assigned_agent=agent,
            )
            .filter(
                Q(state=AgentCommand.State.PENDING)
                | Q(
                    state__in=(
                        AgentCommand.State.DISPATCHED,
                        AgentCommand.State.RUNNING,
                        AgentCommand.State.REPORTING,
                    ),
                    last_progress_at__lt=lease_cutoff,
                )
                | Q(
                    state__in=(
                        AgentCommand.State.DISPATCHED,
                        AgentCommand.State.RUNNING,
                        AgentCommand.State.REPORTING,
                    ),
                    last_progress_at__isnull=True,
                    dispatched_at__lt=lease_cutoff,
                )
            )
            .order_by("requested_at")[:10]
        )
        commands: list[AgentCommand] = []
        for command in candidates:
            accepted_run = (
                CollectionRun.objects.filter(run_id=command.id, source=command.source, agent=agent).first()
                if command.kind == AgentCommand.Kind.RUN_NOW and command.state != AgentCommand.State.PENDING
                else None
            )
            if accepted_run is not None:
                command.state = (
                    AgentCommand.State.SUCCEEDED if accepted_run.state == "complete" else AgentCommand.State.FAILED
                )
                command.completed_at = now
                command.last_progress_at = now
                command.result = {
                    "summary": "Recovered from the accepted collection after the agent lease expired.",
                    "run_id": str(accepted_run.run_id),
                    "collection_state": accepted_run.state,
                    "submission_state": "accepted",
                }
                command.save(update_fields=("state", "completed_at", "last_progress_at", "result"))
                continue
            command.state = AgentCommand.State.DISPATCHED
            command.dispatched_at = now
            command.last_progress_at = now
            command.save(update_fields=("state", "dispatched_at", "last_progress_at"))
            commands.append(command)
    return tuple(
        AgentCommandContract(
            command_id=command.id,
            kind=command.kind,
            requested_at=command.requested_at,
            assignment=_agent_assignment(command.source),
        )
        for command in commands
    )
