from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.contrib.auth.models import AbstractBaseUser
from django.db import IntegrityError, transaction
from django.utils import timezone

from netbox_ssot_contracts import AgentProviderCapability

from .agent_capabilities import reported_capability_issue, serialized_capabilities
from .ingestion.signing import SignatureError, public_key_fingerprint
from .models import (
    AgentCommand,
    AgentEnrollmentToken,
    AgentSecurityEvent,
    AgentSigningKey,
    CollectorAgent,
    DiscoverySource,
)
from .providers import ProviderNotFoundError, ProviderRegistry


class AgentSecurityError(ValueError):
    """Raised when an enrollment or signing-key transition cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class CreatedEnrollment:
    enrollment: AgentEnrollmentToken
    token: str


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_enrollment(
    *,
    agent_name: str,
    sources: Iterable[DiscoverySource],
    created_by: AbstractBaseUser,
    lifetime: timedelta = timedelta(minutes=15),
) -> CreatedEnrollment:
    normalized_name = agent_name.strip()
    selected_sources = tuple(sources)
    if not normalized_name or len(normalized_name) > 100:
        raise AgentSecurityError("Enter an agent name of 100 characters or fewer.")
    now = timezone.now()
    if (
        CollectorAgent.objects.filter(name=normalized_name).exists()
        or AgentEnrollmentToken.objects.filter(
            agent_name=normalized_name,
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=now,
        ).exists()
    ):
        raise AgentSecurityError("An agent or active enrollment already uses this name.")
    raw_token = "nbxssot_" + secrets.token_urlsafe(32)
    with transaction.atomic():
        enrollment = AgentEnrollmentToken.objects.create(
            token_hash=token_digest(raw_token),
            token_prefix=raw_token[:16],
            agent_name=normalized_name,
            created_by=created_by,
            expires_at=now + lifetime,
        )
        enrollment.sources.set(selected_sources)
        AgentSecurityEvent.objects.create(
            kind=AgentSecurityEvent.Kind.ENROLLMENT_CREATED,
            actor=created_by,
            details={
                "enrollment_id": str(enrollment.id),
                "token_prefix": enrollment.token_prefix,
                "agent_name": normalized_name,
                "source_ids": [str(source.id) for source in selected_sources],
                "expires_at": enrollment.expires_at.isoformat(),
            },
        )
    return CreatedEnrollment(enrollment=enrollment, token=raw_token)


def create_replacement_enrollment(
    *,
    agent: CollectorAgent,
    created_by: AbstractBaseUser,
    lifetime: timedelta = timedelta(minutes=15),
) -> CreatedEnrollment:
    now = timezone.now()
    raw_token = "nbxssot_" + secrets.token_urlsafe(32)
    with transaction.atomic():
        locked_agent = CollectorAgent.objects.select_for_update().get(pk=agent.pk)
        AgentEnrollmentToken.objects.filter(
            target_agent=locked_agent,
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__lte=now,
        ).update(revoked_at=now)
        if AgentEnrollmentToken.objects.filter(
            target_agent=locked_agent,
            used_at__isnull=True,
            revoked_at__isnull=True,
        ).exists():
            raise AgentSecurityError("An active replacement enrollment already exists for this agent.")
        enrollment = AgentEnrollmentToken.objects.create(
            token_hash=token_digest(raw_token),
            token_prefix=raw_token[:16],
            agent_name=locked_agent.name,
            target_agent=locked_agent,
            created_by=created_by,
            expires_at=now + lifetime,
        )
        AgentSecurityEvent.objects.create(
            agent=locked_agent,
            kind=AgentSecurityEvent.Kind.REPLACEMENT_CREATED,
            actor=created_by,
            details={
                "enrollment_id": str(enrollment.id),
                "token_prefix": enrollment.token_prefix,
                "expires_at": enrollment.expires_at.isoformat(),
            },
        )
    return CreatedEnrollment(enrollment=enrollment, token=raw_token)


def enroll_agent(
    *,
    token: str,
    public_key: str,
    agent_version: str,
    protocol_version: str,
    providers: tuple[AgentProviderCapability, ...],
) -> tuple[CollectorAgent, AgentSigningKey]:
    try:
        fingerprint = public_key_fingerprint(public_key)
    except SignatureError as exc:
        raise AgentSecurityError("Enrollment request is invalid or unavailable.") from exc
    now = timezone.now()
    with transaction.atomic():
        try:
            enrollment = AgentEnrollmentToken.objects.select_for_update().get(token_hash=token_digest(token))
        except AgentEnrollmentToken.DoesNotExist as exc:
            raise AgentSecurityError("Enrollment request is invalid or unavailable.") from exc
        if enrollment.used_at or enrollment.revoked_at or enrollment.expires_at <= now:
            raise AgentSecurityError("Enrollment request is invalid or unavailable.")
        capabilities = serialized_capabilities(providers)
        target_agent = None
        if enrollment.target_agent_id:
            try:
                target_agent = CollectorAgent.objects.select_for_update().get(pk=enrollment.target_agent_id)
            except CollectorAgent.DoesNotExist as exc:
                raise AgentSecurityError("Enrollment request is invalid or unavailable.") from exc
            sources = tuple(target_agent.sources.all())
        else:
            if CollectorAgent.objects.filter(name=enrollment.agent_name).exists():
                raise AgentSecurityError("Enrollment request is invalid or unavailable.")
            sources = tuple(enrollment.sources.all())
        _validate_provider_capabilities(
            capabilities=capabilities,
            agent_version=agent_version,
            agent_name=enrollment.agent_name,
            sources=sources,
        )
        try:
            if target_agent is None:
                agent = CollectorAgent.objects.create(
                    name=enrollment.agent_name,
                    public_key=public_key,
                    agent_version=agent_version,
                    protocol_version=protocol_version,
                    provider_capabilities=capabilities,
                    capabilities_reported_at=now,
                )
                revoked_keys = 0
            else:
                agent = target_agent
                revoked_keys = AgentSigningKey.objects.filter(
                    agent=agent,
                    state__in=(AgentSigningKey.State.ACTIVE, AgentSigningKey.State.RETIRING),
                ).update(state=AgentSigningKey.State.REVOKED, revoked_at=now, retire_after=None)
                agent.public_key = public_key
                agent.enabled = True
                agent.last_seen_at = None
                agent.agent_version = agent_version
                agent.protocol_version = protocol_version
                agent.provider_capabilities = capabilities
                agent.capabilities_reported_at = now
                agent.save(
                    update_fields=(
                        "public_key",
                        "enabled",
                        "last_seen_at",
                        "agent_version",
                        "protocol_version",
                        "provider_capabilities",
                        "capabilities_reported_at",
                        "updated_at",
                    )
                )
            signing_key = AgentSigningKey.objects.create(
                agent=agent,
                public_key=public_key,
                fingerprint=fingerprint,
            )
        except IntegrityError as exc:
            raise AgentSecurityError("Enrollment request is invalid or unavailable.") from exc
        if target_agent is None:
            enrollment.sources.filter(assigned_agent__isnull=True).update(assigned_agent=agent)
        else:
            AgentCommand.objects.filter(
                agent=agent,
                state__in=("pending", "dispatched", "running", "reporting"),
            ).update(
                state=AgentCommand.State.FAILED,
                completed_at=now,
                result={"summary": "Action cancelled because the agent identity was replaced."},
            )
        enrollment.used_at = now
        enrollment.enrolled_agent = agent
        enrollment.save(update_fields=("used_at", "enrolled_agent"))
        details = {
            "enrollment_id": str(enrollment.id),
            "key_id": str(signing_key.id),
            "fingerprint": signing_key.fingerprint,
            "provider_ids": [capability.provider_id for capability in providers],
        }
        if target_agent is not None:
            details["revoked_key_count"] = revoked_keys
        AgentSecurityEvent.objects.create(
            agent=agent,
            kind=(
                AgentSecurityEvent.Kind.IDENTITY_REPLACED
                if target_agent is not None
                else AgentSecurityEvent.Kind.ENROLLED
            ),
            details=details,
        )
    return agent, signing_key


def _validate_provider_capabilities(
    *,
    capabilities: list[dict[str, object]],
    agent_version: str,
    agent_name: str,
    sources: tuple[DiscoverySource, ...],
) -> None:
    registry = ProviderRegistry()
    for provider_id in sorted({source.provider_id for source in sources}):
        try:
            manifest = registry.get(provider_id).manifest
        except ProviderNotFoundError as exc:
            raise AgentSecurityError("Enrollment request is invalid or unavailable.") from exc
        if reported_capability_issue(capabilities, agent_version, agent_name, manifest):
            raise AgentSecurityError("Enrollment request is invalid or unavailable.")


def rotate_agent_key(
    *,
    agent: CollectorAgent,
    public_key: str,
    grace_period: timedelta,
) -> tuple[AgentSigningKey, datetime, bool]:
    try:
        fingerprint = public_key_fingerprint(public_key)
    except SignatureError as exc:
        raise AgentSecurityError("The replacement public key is invalid.") from exc
    now = timezone.now()
    retire_after = now + grace_period
    with transaction.atomic():
        locked_agent = CollectorAgent.objects.select_for_update().get(pk=agent.pk, enabled=True)
        existing = AgentSigningKey.objects.filter(agent=locked_agent, fingerprint=fingerprint).first()
        if existing and existing.state == AgentSigningKey.State.ACTIVE:
            return existing, retire_after, True
        if existing:
            raise AgentSecurityError("A retired or revoked signing key cannot be reused.")
        AgentSigningKey.objects.filter(
            agent=locked_agent,
            state=AgentSigningKey.State.ACTIVE,
        ).update(state=AgentSigningKey.State.RETIRING, retire_after=retire_after)
        try:
            signing_key = AgentSigningKey.objects.create(
                agent=locked_agent,
                public_key=public_key,
                fingerprint=fingerprint,
            )
        except IntegrityError as exc:
            raise AgentSecurityError("The replacement signing key is already registered.") from exc
        locked_agent.public_key = public_key
        locked_agent.save(update_fields=("public_key", "updated_at"))
        AgentSecurityEvent.objects.create(
            agent=locked_agent,
            kind=AgentSecurityEvent.Kind.KEY_ROTATED,
            details={
                "key_id": str(signing_key.id),
                "fingerprint": signing_key.fingerprint,
                "previous_keys_retire_after": retire_after.isoformat(),
            },
        )
    return signing_key, retire_after, False


def revoke_agent_keys(*, agent: CollectorAgent, actor: AbstractBaseUser) -> int:
    now = timezone.now()
    with transaction.atomic():
        locked_agent = CollectorAgent.objects.select_for_update().get(pk=agent.pk)
        revoked = AgentSigningKey.objects.filter(
            agent=locked_agent,
            state__in=(AgentSigningKey.State.ACTIVE, AgentSigningKey.State.RETIRING),
        ).update(state=AgentSigningKey.State.REVOKED, revoked_at=now, retire_after=None)
        locked_agent.enabled = False
        locked_agent.save(update_fields=("enabled", "updated_at"))
        AgentCommand.objects.filter(
            agent=locked_agent,
            state__in=("pending", "dispatched", "running", "reporting"),
        ).update(
            state=AgentCommand.State.FAILED,
            completed_at=now,
            result={"summary": "Action cancelled because the agent signing keys were revoked."},
        )
        AgentSecurityEvent.objects.create(
            agent=locked_agent,
            kind=AgentSecurityEvent.Kind.KEYS_REVOKED,
            actor=actor,
            details={"revoked_key_count": revoked},
        )
    return revoked
