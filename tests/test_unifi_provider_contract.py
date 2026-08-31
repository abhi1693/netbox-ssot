from __future__ import annotations

from netbox_ssot.providers import ProviderRegistry, build_provider_wizard
from netbox_ssot_contracts import ExecutionMode, FieldOwnershipMode, ProviderCapability, selected_dataset_ids
from netbox_ssot_provider_unifi import provider_definition

MANIFEST = provider_definition().manifest


def test_unifi_provider_is_agent_read_only() -> None:
    assert MANIFEST.provider_id == "unifi"
    assert MANIFEST.implementation_version == "0.0.1"
    assert MANIFEST.execution_modes == (ExecutionMode.AGENT,)
    assert MANIFEST.capabilities == (ProviderCapability.SOURCE_READ,)
    assert MANIFEST.field_ownership is FieldOwnershipMode.OBSERVED
    assert MANIFEST.agent_compatibility.collector_id == "unifi"
    assert MANIFEST.agent_compatibility.protocol_version == "1.0"
    assert MANIFEST.instance_url_field == "api_url"
    assert MANIFEST.secret_fields == ("/api_key_ref",)


def test_wireless_and_interfaces_close_over_required_unifi_datasets() -> None:
    assert selected_dataset_ids(MANIFEST, ("unifi_wireless", "unifi_interfaces")) == (
        "unifi_sites",
        "unifi_devices",
        "unifi_interfaces",
        "unifi_networks",
        "unifi_wireless",
    )


def test_unifi_manifest_maps_only_the_supported_canonical_boundary() -> None:
    assert {
        kind.value
        for dataset in MANIFEST.datasets
        for kind in dataset.resource_kinds
    } == {
        "site",
        "manufacturer",
        "device_role",
        "device_type",
        "device",
        "interface",
        "mac_address",
        "ip_address",
        "vlan",
        "prefix",
        "wireless_lan",
    }
    assert all(dataset.completeness == "declared_scope" for dataset in MANIFEST.datasets)
    assert all(dataset.selectable for dataset in MANIFEST.datasets)


def test_installed_unifi_provider_builds_a_safe_wizard() -> None:
    descriptor = ProviderRegistry().get("unifi")
    wizard = build_provider_wizard(descriptor.manifest)

    assert wizard.provider.display_name == "UniFi Network"
    assert wizard.provider.icon_class == "mdi mdi-access-point-network"
    assert wizard.default_datasets == (
        "unifi_sites",
        "unifi_devices",
        "unifi_interfaces",
        "unifi_networks",
        "unifi_wireless",
    )
    assert tuple(field.name for field in wizard.fields) == (
        "api_url",
        "api_key_ref",
        "site_ref",
        "site_name_override",
        "site_slug_override",
        "verify_tls",
        "page_size",
        "timeout_seconds",
    )
    secret_field = next(field for field in wizard.fields if field.name == "api_key_ref")
    site_field = next(field for field in wizard.fields if field.name == "site_ref")
    assert secret_field.secret is True
    assert secret_field.widget == "secret-reference"
    assert site_field.required is True
