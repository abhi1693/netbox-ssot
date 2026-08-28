from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Protocol

from netbox_ssot_contracts import (
    CURRENT_CONTRACT_VERSION,
    ProviderDefinition,
    ProviderManifest,
    assert_provider_contract,
)

PROVIDER_ENTRY_POINT_GROUP = "netbox_ssot.providers"


class EntryPointLike(Protocol):
    name: str
    value: str

    def load(self) -> Any: ...


EntryPointLoader = Callable[[], Iterable[EntryPointLike]]


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    entry_point_name: str
    distribution_reference: str
    provider: ProviderDefinition
    manifest: ProviderManifest


@dataclass(frozen=True, slots=True)
class ProviderLoadFailure:
    entry_point_name: str
    distribution_reference: str
    error_type: str
    summary: str = "Provider could not be loaded. Inspect server logs for administrator-only details."


@dataclass(frozen=True, slots=True)
class ProviderCatalog:
    providers: tuple[ProviderDescriptor, ...]
    failures: tuple[ProviderLoadFailure, ...]


class ProviderNotFoundError(LookupError):
    pass


class ProviderRegistry:
    """Discover provider packages without accepting runtime import paths from the database."""

    def __init__(self, entry_point_loader: EntryPointLoader | None = None) -> None:
        self._entry_point_loader = entry_point_loader or self._installed_entry_points
        self._catalog: ProviderCatalog | None = None

    def discover(self, *, refresh: bool = False) -> ProviderCatalog:
        if self._catalog is not None and not refresh:
            return self._catalog

        descriptors: list[ProviderDescriptor] = []
        failures: list[ProviderLoadFailure] = []
        seen_provider_ids: set[str] = set()

        for entry_point in sorted(self._entry_point_loader(), key=lambda item: (item.name, item.value)):
            try:
                factory = entry_point.load()
                if not callable(factory):
                    raise TypeError("provider entry point must resolve to a callable factory")
                provider = assert_provider_contract(factory())
                manifest = provider.manifest
                if manifest.contract_version != CURRENT_CONTRACT_VERSION:
                    raise ValueError("provider contract version is not supported")
                if entry_point.name != manifest.provider_id:
                    raise ValueError("entry point name must equal the provider manifest ID")
                if manifest.provider_id in seen_provider_ids:
                    raise ValueError("duplicate provider ID")
                seen_provider_ids.add(manifest.provider_id)
                descriptors.append(
                    ProviderDescriptor(
                        entry_point_name=entry_point.name,
                        distribution_reference=entry_point.value,
                        provider=provider,
                        manifest=manifest,
                    )
                )
            except Exception as exc:
                failures.append(
                    ProviderLoadFailure(
                        entry_point_name=entry_point.name,
                        distribution_reference=entry_point.value,
                        error_type=type(exc).__name__,
                    )
                )

        self._catalog = ProviderCatalog(providers=tuple(descriptors), failures=tuple(failures))
        return self._catalog

    def get(self, provider_id: str) -> ProviderDescriptor:
        for descriptor in self.discover().providers:
            if descriptor.manifest.provider_id == provider_id:
                return descriptor
        raise ProviderNotFoundError(provider_id)

    @staticmethod
    def _installed_entry_points() -> Iterable[EntryPointLike]:
        return metadata.entry_points(group=PROVIDER_ENTRY_POINT_GROUP)
