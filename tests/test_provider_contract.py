from __future__ import annotations

from dataclasses import dataclass

from netbox_ssot.providers import (
    ProviderRegistry,
    build_provider_wizard,
    build_source_model_url,
    build_source_record_url,
)
from netbox_ssot_contracts import ExecutionMode, ProviderCapability, selected_dataset_ids
from netbox_ssot_provider_netbox import provider_definition

MANIFEST = provider_definition().manifest


def test_dataset_selection_adds_dependencies_in_manifest_order() -> None:
    assert selected_dataset_ids(MANIFEST, ("locations",)) == (
        "references",
        "regions",
        "sites",
        "locations",
    )


def test_netbox_provider_is_agent_read_only() -> None:
    assert MANIFEST.implementation_version == "0.0.4"
    assert MANIFEST.execution_modes == (ExecutionMode.AGENT,)
    assert MANIFEST.capabilities == (ProviderCapability.SOURCE_READ,)
    assert MANIFEST.agent_compatibility.collector_id == "netbox"
    assert MANIFEST.agent_compatibility.protocol_version == "1.0"


def test_source_model_url_is_optional_and_rejects_unsafe_instance_urls() -> None:
    assert build_source_model_url("https://netbox.example.com/root", "dcim/sites/") == (
        "https://netbox.example.com/root/dcim/sites/"
    )
    assert build_source_model_url("https://netbox.example.com", None) == ""
    assert build_source_model_url("javascript:alert(1)", "dcim/sites/") == ""
    assert build_source_model_url("https://user:password@netbox.example.com", "dcim/sites/") == ""
    assert build_source_model_url("https://netbox.example.com?token=secret", "dcim/sites/") == ""
    assert build_source_record_url("https://netbox.example.com/root", "dcim/sites/", 42) == (
        "https://netbox.example.com/root/dcim/sites/42/"
    )
    assert build_source_record_url("https://netbox.example.com", "dcim/sites/", "unsafe/id") == (
        "https://netbox.example.com/dcim/sites/unsafe%2Fid/"
    )
    assert build_source_record_url("javascript:alert(1)", "dcim/sites/", 42) == ""


def test_installed_netbox_provider_is_discovered_by_entry_point() -> None:
    catalog = ProviderRegistry().discover()

    assert tuple(item.manifest.provider_id for item in catalog.providers) == ("netbox",)
    assert catalog.failures == ()
    wizard = build_provider_wizard(catalog.providers[0].manifest)
    assert wizard.provider.display_name == "NetBox"
    assert wizard.provider.icon_class == "mdi mdi-cube-outline"
    assert wizard.default_datasets == (
        "regions",
        "sites",
        "locations",
    )
    assert MANIFEST.datasets[0].id == "references"
    assert MANIFEST.datasets[0].selectable is False
    assert tuple(
        (mapping.source_model, mapping.destination_kind.value)
        for dataset in MANIFEST.datasets
        if dataset.selectable
        for mapping in dataset.data_mappings
    ) == (
        ("dcim.Region", "region"),
        ("dcim.Site", "site"),
        ("dcim.Location", "location"),
    )


@dataclass
class ExplodingEntryPoint:
    name: str = "broken"
    value: str = "bad.package:provider"

    def load(self) -> object:
        raise RuntimeError("token=do-not-leak")


def test_provider_load_failure_is_redacted() -> None:
    catalog = ProviderRegistry(entry_point_loader=lambda: (ExplodingEntryPoint(),)).discover()

    assert catalog.providers == ()
    assert len(catalog.failures) == 1
    assert catalog.failures[0].error_type == "RuntimeError"
    assert "do-not-leak" not in repr(catalog.failures[0])
