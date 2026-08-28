from __future__ import annotations

from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from pydantic import Field

from netbox_ssot_contracts import ContractModel, ExecutionMode, ProviderManifest, SchemaField, normalize_config_schema


class ProviderCard(ContractModel):
    provider_id: str
    display_name: str
    icon_class: str
    description: str
    implementation_version: str
    documentation_url: str
    execution_modes: tuple[ExecutionMode, ...]
    datasets: tuple[str, ...]


class ProviderWizard(ContractModel):
    provider: ProviderCard
    fields: tuple[SchemaField, ...]
    default_datasets: tuple[str, ...] = Field(default=())


def build_provider_card(manifest: ProviderManifest) -> ProviderCard:
    return ProviderCard(
        provider_id=manifest.provider_id,
        display_name=manifest.display_name,
        icon_class=manifest.icon_class,
        description=manifest.description,
        implementation_version=manifest.implementation_version,
        documentation_url=str(manifest.documentation_url),
        execution_modes=manifest.execution_modes,
        datasets=tuple(dataset.title for dataset in manifest.datasets if dataset.selectable),
    )


def build_provider_wizard(manifest: ProviderManifest) -> ProviderWizard:
    return ProviderWizard(
        provider=build_provider_card(manifest),
        fields=normalize_config_schema(manifest.config_schema),
        default_datasets=tuple(
            dataset.id for dataset in manifest.datasets if dataset.selectable and dataset.default_enabled
        ),
    )


def build_source_model_url(instance_url: Any, source_path: str | None) -> str:
    if not isinstance(instance_url, str) or not source_path:
        return ""
    parsed = urlsplit(instance_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    if parsed.query or parsed.fragment:
        return ""
    return urljoin(instance_url.rstrip("/") + "/", source_path)


def build_source_record_url(instance_url: Any, source_path: str | None, object_id: Any) -> str:
    model_url = build_source_model_url(instance_url, source_path)
    if not model_url or not isinstance(object_id, (str, int)) or str(object_id) == "":
        return ""
    return urljoin(model_url, quote(str(object_id), safe="") + "/")
