from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse

from netbox_ssot_contracts import ProviderManifest

from .planning.netbox_target import MODEL_BY_KIND
from .providers import build_source_model_url


@dataclass(frozen=True, slots=True)
class DataModelMappingRow:
    dataset_title: str
    source_name: str
    source_model: str
    source_url: str
    destination_name: str
    destination_model: str
    destination_url: str


def selected_data_model_mappings(
    manifest: ProviderManifest,
    selected_dataset_ids: list[str],
    configuration: dict[str, Any],
) -> tuple[DataModelMappingRow, ...]:
    selected = set(selected_dataset_ids)
    instance_url = configuration.get(manifest.instance_url_field, "") if manifest.instance_url_field else ""
    rows: list[DataModelMappingRow] = []
    for dataset in manifest.datasets:
        if not dataset.selectable or dataset.id not in selected:
            continue
        for mapping in dataset.data_mappings:
            model = MODEL_BY_KIND.get(mapping.destination_kind.value)
            if model is None:
                destination_name = mapping.destination_kind.value.replace("_", " ")
                destination_name = destination_name[:1].upper() + destination_name[1:]
                destination_model = mapping.destination_kind.value
                destination_url = ""
            else:
                destination_name = str(model._meta.verbose_name)
                destination_name = destination_name[:1].upper() + destination_name[1:]
                destination_model = model._meta.label
                try:
                    destination_url = reverse(f"{model._meta.app_label}:{model._meta.model_name}_list")
                except NoReverseMatch:
                    destination_url = ""
            rows.append(
                DataModelMappingRow(
                    dataset_title=dataset.title,
                    source_name=mapping.source_name,
                    source_model=mapping.source_model,
                    source_url=build_source_model_url(instance_url, mapping.source_path),
                    destination_name=destination_name,
                    destination_model=destination_model,
                    destination_url=destination_url,
                )
            )
    return tuple(rows)
