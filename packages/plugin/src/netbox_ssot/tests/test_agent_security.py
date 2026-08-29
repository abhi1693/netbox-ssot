from __future__ import annotations

import base64
import json
from datetime import timedelta
from time import time
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from netbox_ssot.agent_security import (
    AgentSecurityError,
    create_enrollment,
    create_replacement_enrollment,
    enroll_agent,
    revoke_agent_keys,
    rotate_agent_key,
    token_digest,
)
from netbox_ssot.health import source_health
from netbox_ssot.ingestion.signing import signing_payload
from netbox_ssot.models import (
    AgentCommand,
    AgentEnrollmentToken,
    AgentSecurityEvent,
    AgentSigningKey,
    CollectionRun,
    CollectorAgent,
    DiscoverySource,
)
from netbox_ssot_contracts import AgentProviderCapability

NETBOX_CAPABILITIES = (
    AgentProviderCapability(
        provider_id="netbox",
        implementation_version="0.0.11",
        contract_version="1.0",
    ),
)
PAUSE_CONFIG = {
    "netbox_ssot": {
        "agent_signature_max_age_seconds": 300,
        "maximum_batch_bytes": 67_108_864,
        "pause_scheduled_collections_until_resolved": True,
    }
}


def public_key() -> str:
    raw = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def key_pair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    encoded = base64.urlsafe_b64encode(private_key.public_key().public_bytes_raw()).rstrip(b"=").decode()
    return private_key, encoded


def signed_headers(private_key: Ed25519PrivateKey, agent_id: object, body: bytes) -> dict[str, str]:
    timestamp = int(time())
    signature = base64.urlsafe_b64encode(
        private_key.sign(signing_payload(agent_id, timestamp, body)),
    ).rstrip(b"=")
    return {
        "HTTP_X_NETBOX_SSOT_AGENT": str(agent_id),
        "HTTP_X_NETBOX_SSOT_TIMESTAMP": str(timestamp),
        "HTTP_X_NETBOX_SSOT_SIGNATURE": signature.decode(),
    }


class AgentSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create_user(username="agent-security-user")

    def setUp(self) -> None:
        self.source = DiscoverySource.objects.create(
            name="security-source",
            provider_id="netbox",
            configuration={"token_ref": "env://NEVER_PERSIST_THE_TOKEN_VALUE"},
            datasets=["regions"],
        )

    def test_one_time_enrollment_stores_only_token_digest(self) -> None:
        created = create_enrollment(agent_name="edge-1", sources=[self.source], created_by=self.user)

        enrollment = AgentEnrollmentToken.objects.get(pk=created.enrollment.pk)
        assert enrollment.token_hash != created.token
        assert enrollment.token_hash == token_digest(created.token)
        assert not AgentEnrollmentToken.objects.filter(token_hash__contains=created.token).exists()

        agent, key = enroll_agent(
            token=created.token,
            public_key=public_key(),
            agent_version="0.6.3-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )

        enrollment.refresh_from_db()
        self.source.refresh_from_db()
        assert enrollment.enrolled_agent == agent
        assert enrollment.used_at is not None
        assert self.source.assigned_agent == agent
        assert key.state == AgentSigningKey.State.ACTIVE
        assert AgentSecurityEvent.objects.filter(agent=agent, kind="enrolled").exists()
        with pytest.raises(AgentSecurityError, match="invalid or unavailable"):
            enroll_agent(
                token=created.token,
                public_key=public_key(),
                agent_version="0.6.3-alpha.0",
                protocol_version="1.1",
                providers=NETBOX_CAPABILITIES,
            )

    def test_expired_enrollment_is_rejected_without_revealing_state(self) -> None:
        created = create_enrollment(
            agent_name="expired-edge",
            sources=[self.source],
            created_by=self.user,
            lifetime=timedelta(seconds=-1),
        )

        with pytest.raises(AgentSecurityError, match="invalid or unavailable"):
            enroll_agent(
                token=created.token,
                public_key=public_key(),
                agent_version="0.6.3-alpha.0",
                protocol_version="1.1",
                providers=NETBOX_CAPABILITIES,
            )

    def test_rotation_overlap_and_revocation_preserve_audit_history(self) -> None:
        created = create_enrollment(agent_name="rotating-edge", sources=[self.source], created_by=self.user)
        agent, original = enroll_agent(
            token=created.token,
            public_key=public_key(),
            agent_version="0.6.3-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )

        replacement, retire_after, duplicate = rotate_agent_key(
            agent=agent,
            public_key=public_key(),
            grace_period=timedelta(minutes=10),
        )

        original.refresh_from_db()
        agent.refresh_from_db()
        assert not duplicate
        assert original.state == AgentSigningKey.State.RETIRING
        assert original.retire_after == retire_after
        assert replacement.state == AgentSigningKey.State.ACTIVE
        assert agent.public_key == replacement.public_key
        assert retire_after > timezone.now()

        revoked = revoke_agent_keys(agent=agent, actor=self.user)

        agent.refresh_from_db()
        original.refresh_from_db()
        replacement.refresh_from_db()
        assert revoked == 2
        assert not agent.enabled
        assert original.state == AgentSigningKey.State.REVOKED
        assert replacement.state == AgentSigningKey.State.REVOKED
        assert list(agent.security_events.values_list("kind", flat=True)) == ["keys_revoked", "key_rotated", "enrolled"]

    def test_signed_rotation_keeps_previous_key_valid_during_overlap(self) -> None:
        original_private, original_public = key_pair()
        created = create_enrollment(agent_name="api-edge", sources=[self.source], created_by=self.user)
        agent, _ = enroll_agent(
            token=created.token,
            public_key=original_public,
            agent_version="0.6.3-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )
        _, replacement_public = key_pair()
        rotation_body = json.dumps(
            {"public_key": replacement_public, "agent_version": "0.6.3-alpha.0"},
            separators=(",", ":"),
        ).encode()
        client = APIClient()

        response = client.post(
            reverse("plugins-api:netbox_ssot-api:agent-key-rotate"),
            data=rotation_body,
            content_type="application/json",
            **signed_headers(original_private, agent.id, rotation_body),
        )

        assert response.status_code == 200, response.content
        assert response.json()["status"] == "rotated"
        configuration_body = json.dumps(
            {
                "protocol_version": "1.1",
                "agent_version": "0.6.3-alpha.0",
                "control_interval_seconds": 5,
                "active_command_ids": [],
            },
            separators=(",", ":"),
        ).encode()
        overlap_response = client.post(
            reverse("plugins-api:netbox_ssot-api:agent-config"),
            data=configuration_body,
            content_type="application/json",
            **signed_headers(original_private, agent.id, configuration_body),
        )

        assert overlap_response.status_code == 200
        assert "schedule_enabled" not in overlap_response.json()["assignments"][0]
        agent.refresh_from_db()
        assert agent.provider_capabilities == [capability.model_dump(mode="json") for capability in NETBOX_CAPABILITIES]
        revoke_agent_keys(agent=agent, actor=self.user)
        revoked_response = client.post(
            reverse("plugins-api:netbox_ssot-api:agent-config"),
            data=configuration_body,
            content_type="application/json",
            **signed_headers(original_private, agent.id, configuration_body),
        )
        assert revoked_response.status_code in {401, 403}

    @override_settings(PLUGINS_CONFIG=PAUSE_CONFIG)
    def test_current_agent_receives_paused_schedule_while_commands_remain_available(self) -> None:
        private_key, encoded_public_key = key_pair()
        created = create_enrollment(agent_name="pause-api-edge", sources=[self.source], created_by=self.user)
        agent, _ = enroll_agent(
            token=created.token,
            public_key=encoded_public_key,
            agent_version="0.6.8-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )
        completed_at = timezone.now()
        run = CollectionRun.objects.create(
            run_id=uuid4(),
            source=self.source,
            agent=agent,
            provider_id="netbox",
            provider_version="0.0.4",
            contract_version="1.0",
            state="complete",
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            datasets=["regions"],
            scope=[],
            messages=[],
            completeness_token="complete",
            payload_digest="a" * 64,
            observation_count=1,
        )
        command = AgentCommand.objects.create(
            agent=agent,
            source=self.source,
            kind=AgentCommand.Kind.TEST_CONNECTION,
            requested_by=self.user,
        )
        body = json.dumps(
            {
                "protocol_version": "1.1",
                "agent_version": "0.6.8-alpha.0",
                "control_interval_seconds": 5,
                "active_command_ids": [],
                "providers": [capability.model_dump(mode="json") for capability in NETBOX_CAPABILITIES],
            },
            separators=(",", ":"),
        ).encode()

        response = APIClient().post(
            reverse("plugins-api:netbox_ssot-api:agent-config"),
            data=body,
            content_type="application/json",
            **signed_headers(private_key, agent.id, body),
        )

        assert response.status_code == 200, response.content
        assignment = response.json()["assignments"][0]
        assert assignment["schedule_enabled"] is False
        assert str(run.run_id) in assignment["schedule_pause_reason"]
        assert response.json()["commands"][0]["command_id"] == str(command.id)

    def test_standby_enrollment_can_connect_without_sources(self) -> None:
        created = create_enrollment(agent_name="standby-edge", sources=[], created_by=self.user)

        agent, _ = enroll_agent(
            token=created.token,
            public_key=public_key(),
            agent_version="0.6.5-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )

        assert not agent.sources.exists()
        assert agent.provider_capabilities[0]["provider_id"] == "netbox"

    def test_enrollment_rejects_agent_without_assigned_provider(self) -> None:
        created = create_enrollment(agent_name="incompatible-edge", sources=[self.source], created_by=self.user)

        with pytest.raises(AgentSecurityError, match="invalid or unavailable"):
            enroll_agent(
                token=created.token,
                public_key=public_key(),
                agent_version="0.6.5-alpha.0",
                protocol_version="1.1",
                providers=(),
            )

        assert created.enrollment.used_at is None
        assert self.source.assigned_agent is None

    def test_capability_change_suppresses_incompatible_assignment_and_records_activity(self) -> None:
        private_key, encoded_public_key = key_pair()
        created = create_enrollment(agent_name="changed-edge", sources=[self.source], created_by=self.user)
        agent, _ = enroll_agent(
            token=created.token,
            public_key=encoded_public_key,
            agent_version="0.6.5-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )
        command = AgentCommand.objects.create(
            agent=agent,
            source=self.source,
            kind=AgentCommand.Kind.TEST_CONNECTION,
            requested_by=self.user,
        )
        body = json.dumps(
            {
                "protocol_version": "1.1",
                "agent_version": "0.6.5-alpha.0",
                "control_interval_seconds": 5,
                "active_command_ids": [],
                "providers": [],
            },
            separators=(",", ":"),
        ).encode()

        response = APIClient().post(
            reverse("plugins-api:netbox_ssot-api:agent-config"),
            data=body,
            content_type="application/json",
            **signed_headers(private_key, agent.id, body),
        )

        assert response.status_code == 200
        assert response.json()["assignments"] == []
        assert response.json()["commands"] == []
        command.refresh_from_db()
        assert command.state == AgentCommand.State.PENDING
        assert command.dispatched_at is None
        agent.refresh_from_db()
        self.source.refresh_from_db()
        assert agent.provider_capabilities == []
        assert source_health(self.source).status.key == "incompatible_agent"
        event = AgentSecurityEvent.objects.get(agent=agent, kind="capabilities_updated")
        assert event.details["incompatible_source_ids"] == [str(self.source.id)]

    def test_replacement_preserves_agent_and_sources_while_revoking_old_key(self) -> None:
        created = create_enrollment(agent_name="replace-edge", sources=[self.source], created_by=self.user)
        agent, original = enroll_agent(
            token=created.token,
            public_key=public_key(),
            agent_version="0.6.5-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )
        replacement = create_replacement_enrollment(agent=agent, created_by=self.user)

        replaced_agent, replacement_key = enroll_agent(
            token=replacement.token,
            public_key=public_key(),
            agent_version="0.6.5-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )

        original.refresh_from_db()
        self.source.refresh_from_db()
        assert replaced_agent.pk == agent.pk
        assert self.source.assigned_agent_id == agent.pk
        assert original.state == AgentSigningKey.State.REVOKED
        assert replacement_key.state == AgentSigningKey.State.ACTIVE
        assert AgentSecurityEvent.objects.filter(agent=agent, kind="identity_replaced").exists()

    def test_source_edit_reassigns_to_compatible_standby_and_records_actor(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=("is_superuser",))
        current_enrollment = create_enrollment(agent_name="current-edge", sources=[self.source], created_by=self.user)
        current_agent, _ = enroll_agent(
            token=current_enrollment.token,
            public_key=public_key(),
            agent_version="0.6.5-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )
        standby_enrollment = create_enrollment(agent_name="standby-target", sources=[], created_by=self.user)
        standby_agent, _ = enroll_agent(
            token=standby_enrollment.token,
            public_key=public_key(),
            agent_version="0.6.5-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )
        client = Client()
        client.force_login(self.user)

        response = client.post(
            reverse("plugins:netbox_ssot:source_edit", kwargs={"pk": self.source.pk}),
            data={
                "source_name": self.source.name,
                "base_url": "https://source.example.com",
                "token_ref": "env://NEVER_PERSIST_THE_TOKEN_VALUE",
                "datasets": ["regions"],
                "assigned_agent": str(standby_agent.pk),
                "collection_interval_minutes": "60",
                "retention_days": "30",
                "retention_successful_runs": "10000",
                "retention_failure_days": "30",
                "enabled": "on",
            },
        )

        assert response.status_code == 302
        self.source.refresh_from_db()
        assert self.source.assigned_agent == standby_agent
        event = AgentSecurityEvent.objects.get(kind="source_reassigned", agent=standby_agent)
        assert event.actor == self.user
        assert event.details["previous_agent_id"] == str(current_agent.pk)

    def test_replacement_page_creates_one_time_command(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=("is_superuser",))
        created = create_enrollment(agent_name="replace-ui-edge", sources=[], created_by=self.user)
        agent, _ = enroll_agent(
            token=created.token,
            public_key=public_key(),
            agent_version="0.6.5-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )
        client = Client()
        client.force_login(self.user)

        response = client.post(reverse("plugins:netbox_ssot:agent_replace", kwargs={"pk": agent.pk}))

        assert response.status_code == 200
        assert b"NETBOX_SSOT_ENROLLMENT_TOKEN" in response.content
        assert response.headers["Cache-Control"] == "no-store"
        assert AgentEnrollmentToken.objects.filter(target_agent=agent, used_at__isnull=True).exists()

    def test_agent_settings_rotates_root_owned_key_with_sudo(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=("is_superuser",))
        agent = CollectorAgent.objects.create(
            name="rotation-ui-edge",
            public_key=public_key(),
        )
        client = Client()
        client.force_login(self.user)

        response = client.get(reverse("plugins:netbox_ssot:agent_edit", kwargs={"pk": agent.pk}))

        assert response.status_code == 200
        assert b"sudo netbox-ssot-agent rotate-key" in response.content
        assert b"file:///etc/netbox-ssot-agent/signing-key" in response.content

    def test_replacement_enrollment_is_single_active_token_but_expiry_can_be_replaced(self) -> None:
        created = create_enrollment(agent_name="replacement-token-edge", sources=[], created_by=self.user)
        agent, _ = enroll_agent(
            token=created.token,
            public_key=public_key(),
            agent_version="0.6.5-alpha.0",
            protocol_version="1.1",
            providers=NETBOX_CAPABILITIES,
        )
        expired = create_replacement_enrollment(
            agent=agent,
            created_by=self.user,
            lifetime=timedelta(seconds=-1),
        )

        current = create_replacement_enrollment(agent=agent, created_by=self.user)

        expired.enrollment.refresh_from_db()
        assert expired.enrollment.revoked_at is not None
        assert current.enrollment.revoked_at is None
        with pytest.raises(AgentSecurityError, match="active replacement"):
            create_replacement_enrollment(agent=agent, created_by=self.user)
