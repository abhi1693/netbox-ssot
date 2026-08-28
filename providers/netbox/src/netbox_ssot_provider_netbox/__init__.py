from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path

from netbox_ssot_contracts import ProviderManifest


@lru_cache(maxsize=1)
def _manifest() -> ProviderManifest:
    manifest_path = resources.files(__package__).joinpath("manifest.json")
    if manifest_path.is_file():
        payload = manifest_path.read_text(encoding="utf-8")
    else:
        payload = Path(__file__).resolve().parents[2].joinpath("manifest.json").read_text(encoding="utf-8")
    return ProviderManifest.model_validate_json(payload)


class NetBoxProviderDefinition:
    @property
    def manifest(self) -> ProviderManifest:
        return _manifest()


def provider_definition() -> NetBoxProviderDefinition:
    return NetBoxProviderDefinition()


__all__ = ["NetBoxProviderDefinition", "provider_definition"]
