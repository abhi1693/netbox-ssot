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
    assert MANIFEST.implementation_version == "0.0.9"
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
        "users",
        "regions",
        "sites",
        "locations",
        "device_catalog",
        "racks",
        "module_catalog",
        "component_templates",
        "devices",
        "device_components",
        "rack_reservations",
        "power",
        "circuit_catalog",
        "circuits",
        "virtual_circuits",
        "circuit_group_assignments",
        "cabling",
    )
    assert MANIFEST.datasets[0].id == "references"
    assert MANIFEST.datasets[0].selectable is False
    assert tuple(
        (mapping.source_model, mapping.destination_kind.value)
        for dataset in MANIFEST.datasets
        if dataset.selectable
        for mapping in dataset.data_mappings
    ) == (
        ("users.ObjectPermission", "object_permission"),
        ("users.Group", "user_group"),
        ("users.User", "user"),
        ("dcim.Region", "region"),
        ("dcim.Site", "site"),
        ("dcim.Location", "location"),
        ("dcim.Manufacturer", "manufacturer"),
        ("dcim.DeviceRole", "device_role"),
        ("dcim.Platform", "platform"),
        ("dcim.DeviceType", "device_type"),
        ("dcim.RackGroup", "rack_group"),
        ("dcim.RackRole", "rack_role"),
        ("dcim.RackType", "rack_type"),
        ("dcim.Rack", "rack"),
        ("dcim.ModuleTypeProfile", "module_type_profile"),
        ("dcim.ModuleType", "module_type"),
        ("dcim.InventoryItemRole", "inventory_item_role"),
        ("dcim.ConsolePortTemplate", "console_port_template"),
        ("dcim.ConsoleServerPortTemplate", "console_server_port_template"),
        ("dcim.PowerPortTemplate", "power_port_template"),
        ("dcim.PowerOutletTemplate", "power_outlet_template"),
        ("dcim.InterfaceTemplate", "interface_template"),
        ("dcim.RearPortTemplate", "rear_port_template"),
        ("dcim.FrontPortTemplate", "front_port_template"),
        ("dcim.ModuleBayTemplate", "module_bay_template"),
        ("dcim.DeviceBayTemplate", "device_bay_template"),
        ("dcim.InventoryItemTemplate", "inventory_item_template"),
        ("dcim.VirtualChassis", "virtual_chassis"),
        ("dcim.Device", "device"),
        ("dcim.VirtualDeviceContext", "virtual_device_context"),
        ("dcim.ModuleBay", "module_bay"),
        ("dcim.DeviceBay", "device_bay"),
        ("dcim.Module", "module"),
        ("dcim.ConsolePort", "console_port"),
        ("dcim.ConsoleServerPort", "console_server_port"),
        ("dcim.PowerPort", "power_port"),
        ("dcim.PowerOutlet", "power_outlet"),
        ("dcim.Interface", "interface"),
        ("dcim.RearPort", "rear_port"),
        ("dcim.FrontPort", "front_port"),
        ("dcim.InventoryItem", "inventory_item"),
        ("dcim.MACAddress", "mac_address"),
        ("dcim.RackReservation", "rack_reservation"),
        ("dcim.PowerPanel", "power_panel"),
        ("dcim.PowerFeed", "power_feed"),
        ("circuits.Provider", "provider"),
        ("circuits.ProviderAccount", "provider_account"),
        ("circuits.ProviderNetwork", "provider_network"),
        ("circuits.CircuitType", "circuit_type"),
        ("circuits.VirtualCircuitType", "virtual_circuit_type"),
        ("circuits.CircuitGroup", "circuit_group"),
        ("circuits.Circuit", "circuit"),
        ("circuits.CircuitTermination", "circuit_termination"),
        ("circuits.VirtualCircuit", "virtual_circuit"),
        ("circuits.VirtualCircuitTermination", "virtual_circuit_termination"),
        ("circuits.CircuitGroupAssignment", "circuit_group_assignment"),
        ("dcim.CableBundle", "cable_bundle"),
        ("dcim.Cable", "cable"),
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
