from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.urls import NoReverseMatch, reverse

from .models import DiscoverySource
from .providers import ProviderNotFoundError, ProviderRegistry, build_source_record_url


@dataclass(frozen=True, slots=True)
class RecordLinks:
    source_url: str = ""
    destination_url: str = ""


class RecordLinkResolver:
    def __init__(self, source: DiscoverySource) -> None:
        self._instance_url: Any = ""
        self._source_paths: dict[str, str | None] = {}
        try:
            manifest = ProviderRegistry().get(source.provider_id).manifest
        except ProviderNotFoundError:
            return
        if manifest.instance_url_field:
            self._instance_url = source.configuration.get(manifest.instance_url_field, "")
        for dataset in manifest.datasets:
            for mapping in dataset.data_mappings:
                self._source_paths[mapping.destination_kind.value.casefold()] = mapping.source_path
                self._source_paths[mapping.source_model.casefold()] = mapping.source_path

    def resolve(
        self,
        *,
        resource_kind: str,
        source_object_id: str,
        target_object_type: str,
        target_object_id: str,
    ) -> RecordLinks:
        return RecordLinks(
            source_url=build_source_record_url(
                self._instance_url,
                self._source_paths.get(resource_kind.casefold()),
                source_object_id,
            ),
            destination_url=_destination_record_url(target_object_type, target_object_id),
        )


def source_object_id(evidence: Any) -> str:
    if not isinstance(evidence, list):
        return ""
    for item in evidence:
        if not isinstance(item, dict):
            continue
        value = item.get("source_object_id")
        if isinstance(value, (str, int)) and str(value):
            return str(value)
    return ""


def _destination_record_url(target_object_type: str, target_object_id: str) -> str:
    if not target_object_type or not target_object_id:
        return ""
    try:
        model = apps.get_model(target_object_type)
        if model is None:
            return ""
        return reverse(
            f"{model._meta.app_label}:{model._meta.model_name}",
            kwargs={"pk": target_object_id},
        )
    except (LookupError, NoReverseMatch, ValueError):
        return ""
