from __future__ import annotations

from typing import Any

from netbox_ssot_contracts import AgentProviderCapability, ProviderManifest

from .models import CollectorAgent
from .providers import ProviderNotFoundError, ProviderRegistry


def serialized_capabilities(capabilities: tuple[AgentProviderCapability, ...]) -> list[dict[str, str]]:
    return [capability.model_dump(mode="json") for capability in capabilities]


def capability_issue(agent: CollectorAgent, manifest: ProviderManifest) -> str:
    capabilities = agent.provider_capabilities if isinstance(agent.provider_capabilities, list) else []
    return reported_capability_issue(capabilities, agent.agent_version, agent.name, manifest)


def reported_capability_issue(
    capabilities: list[dict[str, Any]],
    agent_version: str,
    agent_name: str,
    manifest: ProviderManifest,
) -> str:
    capability = next(
        (item for item in capabilities if isinstance(item, dict) and item.get("provider_id") == manifest.provider_id),
        None,
    )
    if capability is None:
        return f"{agent_name} has not advertised the {manifest.display_name} collector."
    if capability.get("contract_version") != manifest.contract_version:
        return f"{agent_name} uses an incompatible {manifest.display_name} contract."
    if capability.get("implementation_version") != manifest.implementation_version:
        return (
            f"{agent_name} has {manifest.display_name} collector "
            f"{capability.get('implementation_version', 'unknown')}; {manifest.implementation_version} is required."
        )
    if not _version_at_least(agent_version, manifest.agent_compatibility.minimum_agent_version):
        return f"{agent_name} must be upgraded before it can run this provider."
    return ""


def supports_provider(agent: CollectorAgent, manifest: ProviderManifest) -> bool:
    return not capability_issue(agent, manifest)


def source_capability_issue(agent: CollectorAgent, provider_id: str) -> str:
    try:
        manifest = ProviderRegistry().get(provider_id).manifest
    except ProviderNotFoundError:
        return f"Provider {provider_id} is not installed."
    return capability_issue(agent, manifest)


def agent_capability_rows(agent: CollectorAgent) -> tuple[dict[str, Any], ...]:
    capabilities = agent.provider_capabilities if isinstance(agent.provider_capabilities, list) else []
    return tuple(item for item in capabilities if isinstance(item, dict))


def _version_at_least(value: str, minimum: str) -> bool:
    try:
        current_parts = value.split("-", 1)[0].split("+")[0].split(".")
        minimum_parts = minimum.split("-", 1)[0].split("+")[0].split(".")
        current = tuple(int(part) for part in current_parts)
        required = tuple(int(part) for part in minimum_parts)
    except ValueError:
        return False
    return len(current) == 3 and len(required) == 3 and current >= required


__all__ = [
    "agent_capability_rows",
    "capability_issue",
    "reported_capability_issue",
    "serialized_capabilities",
    "source_capability_issue",
    "supports_provider",
]
