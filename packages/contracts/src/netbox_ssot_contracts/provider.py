from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from .base import ContractModel, Identifier
from .manifest import ExecutionMode, ProviderManifest
from .observation import CollectionMessage, ScopeDimension


class CollectionRequest(ContractModel):
    run_id: UUID
    source_id: UUID
    provider_id: Identifier
    execution_mode: ExecutionMode
    datasets: tuple[Identifier, ...] = Field(min_length=1)
    scope: tuple[ScopeDimension, ...] = ()
    configuration: dict[str, Any] = Field(default_factory=dict)


class ConnectionTestRequest(ContractModel):
    source_id: UUID
    provider_id: Identifier
    execution_mode: ExecutionMode
    configuration: dict[str, Any] = Field(default_factory=dict)


class ConnectionTestResult(ContractModel):
    succeeded: bool
    summary: str = Field(min_length=1, max_length=500)
    details: tuple[CollectionMessage, ...] = ()


@runtime_checkable
class ProviderDefinition(Protocol):
    @property
    def manifest(self) -> ProviderManifest:
        """Return the static descriptor matched by a compiled Go collector."""


def assert_provider_contract(provider: object) -> ProviderDefinition:
    if not isinstance(provider, ProviderDefinition):
        raise TypeError("provider does not implement the ProviderDefinition protocol")
    return provider


def selected_dataset_ids(manifest: ProviderManifest, requested: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
    known = {dataset.id: dataset for dataset in manifest.datasets}
    unknown = set(requested) - set(known)
    if unknown:
        raise ValueError(f"unknown datasets requested: {sorted(unknown)}")

    selected = set(requested)
    pending = list(requested)
    while pending:
        dataset_id = pending.pop()
        for dependency in known[dataset_id].depends_on:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return tuple(dataset.id for dataset in manifest.datasets if dataset.id in selected)
