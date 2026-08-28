from __future__ import annotations

from dataclasses import dataclass
from time import time
from uuid import UUID

from django.db.models import Q
from django.utils import timezone
from netbox.plugins import get_plugin_config
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from ..ingestion.signing import AGENT_HEADER, SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, verify_signature
from ..models import AgentSigningKey, CollectorAgent


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    agent_id: UUID
    name: str
    key_id: UUID
    is_active: bool = True
    is_authenticated: bool = True
    is_anonymous: bool = False


class AgentSignatureAuthentication(BaseAuthentication):
    def authenticate(self, request: object) -> tuple[AgentPrincipal, CollectorAgent] | None:
        headers = request.headers
        agent_value = headers.get(AGENT_HEADER)
        timestamp_value = headers.get(TIMESTAMP_HEADER)
        signature = headers.get(SIGNATURE_HEADER)
        if not any((agent_value, timestamp_value, signature)):
            return None
        if not all((agent_value, timestamp_value, signature)):
            raise AuthenticationFailed("Agent authentication failed.")

        try:
            agent_id = UUID(agent_value)
            timestamp = int(timestamp_value)
            maximum_age = int(get_plugin_config("netbox_ssot", "agent_signature_max_age_seconds"))
            if abs(int(time()) - timestamp) > maximum_age:
                raise SignatureError("signature timestamp is outside the accepted window")
            maximum_bytes = int(get_plugin_config("netbox_ssot", "maximum_batch_bytes"))
            body = request.body
            if len(body) > maximum_bytes:
                raise SignatureError("signed body exceeds the configured size limit")
            agent = CollectorAgent.objects.get(pk=agent_id, enabled=True)
            now = timezone.now()
            AgentSigningKey.objects.filter(
                agent=agent,
                state=AgentSigningKey.State.RETIRING,
                retire_after__lte=now,
            ).update(state=AgentSigningKey.State.RETIRED, retired_at=now)
            signing_keys = tuple(
                AgentSigningKey.objects.filter(agent=agent).filter(
                    Q(state=AgentSigningKey.State.ACTIVE)
                    | Q(state=AgentSigningKey.State.RETIRING, retire_after__gt=now)
                )
            )
            authenticated_key = None
            for signing_key in signing_keys:
                try:
                    verify_signature(
                        public_key=signing_key.public_key,
                        agent_id=agent_id,
                        timestamp=timestamp,
                        body=body,
                        signature=signature,
                    )
                except SignatureError:
                    continue
                authenticated_key = signing_key
                break
            if authenticated_key is None:
                raise SignatureError("agent signature is invalid")
            AgentSigningKey.objects.filter(pk=authenticated_key.pk).update(last_used_at=now)
        except (CollectorAgent.DoesNotExist, SignatureError, TypeError, ValueError) as exc:
            raise AuthenticationFailed("Agent authentication failed.") from exc

        return AgentPrincipal(agent_id=agent.id, name=agent.name, key_id=authenticated_key.id), agent

    def authenticate_header(self, request: object) -> str:
        return "NetBox-SSoT-Signature"
