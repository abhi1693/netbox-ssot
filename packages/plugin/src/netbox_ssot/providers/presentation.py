from __future__ import annotations

from collections.abc import Iterable
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


class ProviderDataset(ContractModel):
    id: str
    title: str
    description: str


class ProviderDatasetGroup(ContractModel):
    id: str
    title: str
    datasets: tuple[ProviderDataset, ...]


class ProviderWizard(ContractModel):
    provider: ProviderCard
    fields: tuple[SchemaField, ...]
    dataset_groups: tuple[ProviderDatasetGroup, ...] = Field(default=())
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
        dataset_groups=build_provider_dataset_groups(manifest),
        default_datasets=tuple(
            dataset.id for dataset in manifest.datasets if dataset.selectable and dataset.default_enabled
        ),
    )


def build_provider_dataset_groups(
    manifest: ProviderManifest,
    dataset_ids: Iterable[str] | None = None,
    *,
    include_supporting: bool = False,
) -> tuple[ProviderDatasetGroup, ...]:
    selected_ids = set(dataset_ids) if dataset_ids is not None else None
    grouped: dict[str, list[ProviderDataset]] = {}
    for dataset in manifest.datasets:
        if selected_ids is not None and dataset.id not in selected_ids:
            continue
        if not dataset.selectable:
            if not include_supporting:
                continue
            source_namespace = "dependencies"
        else:
            first_mapping = dataset.data_mappings[0] if dataset.data_mappings else None
            source_namespace = first_mapping.source_model.partition(".")[0].lower() if first_mapping else "other"
        grouped.setdefault(source_namespace, []).append(
            ProviderDataset(
                id=dataset.id,
                title=dataset.title,
                description=dataset.description,
            )
        )
    acronyms = {"dcim": "DCIM", "ipam": "IPAM", "vpn": "VPN", "dependencies": "Dependencies"}
    return tuple(
        ProviderDatasetGroup(
            id=group_id,
            title=acronyms.get(group_id, group_id.replace("_", " ").title()),
            datasets=tuple(datasets),
        )
        for group_id, datasets in grouped.items()
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
