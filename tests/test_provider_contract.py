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


def test_ipam_services_selection_closes_over_routing_vlan_and_dcim_dependencies() -> None:
    selected = selected_dataset_ids(MANIFEST, ("ipam_services",))

    assert set(selected) >= {
        "references",
        "ipam_routing",
        "ipam_vlans",
        "ipam_prefixes",
        "ipam_addresses",
        "ipam_services",
        "locations",
        "racks",
        "devices",
        "device_components",
        "virtualization_clusters",
        "virtualization_machines",
        "virtualization_components",
    }


def test_contact_assignments_close_over_every_supported_target_dataset() -> None:
    selected = set(selected_dataset_ids(MANIFEST, ("tenancy_contact_assignments",)))

    assert {
        "references",
        "tenancy_contacts",
        "tenancy_contact_assignments",
        "ipam_registries",
        "ipam_services",
        "regions",
        "locations",
        "device_catalog",
        "racks",
        "devices",
        "power",
        "circuit_catalog",
        "circuits",
        "virtual_circuits",
        "virtualization_clusters",
        "virtualization_machines",
        "vpn_crypto",
        "vpn_tunnels",
        "vpn_l2vpns",
    } <= selected


def test_virtualization_components_close_over_placement_vlan_and_routing_dependencies() -> None:
    selected = set(selected_dataset_ids(MANIFEST, ("virtualization_components",)))

    assert {
        "references",
        "ipam_routing",
        "ipam_vlans",
        "locations",
        "racks",
        "device_catalog",
        "devices",
        "extras_templates",
        "virtualization_clusters",
        "virtualization_machines",
        "virtualization_components",
    } <= selected


def test_vpn_tunnels_close_over_crypto_addresses_and_interface_dependencies() -> None:
    selected = set(selected_dataset_ids(MANIFEST, ("vpn_tunnels",)))

    assert {
        "references",
        "vpn_crypto",
        "vpn_tunnels",
        "ipam_addresses",
        "device_components",
        "virtualization_components",
    } <= selected


def test_l2vpns_close_over_route_targets_vlans_and_interface_dependencies() -> None:
    selected = set(selected_dataset_ids(MANIFEST, ("vpn_l2vpns",)))

    assert {
        "references",
        "vpn_l2vpns",
        "ipam_routing",
        "ipam_vlans",
        "device_components",
        "virtualization_components",
    } <= selected


def test_netbox_provider_is_agent_read_only() -> None:
    assert MANIFEST.implementation_version == "0.0.15"
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
        "tenancy_contacts",
        "tenancy_contact_assignments",
        "ipam_registries",
        "ipam_routing",
        "ipam_vlans",
        "ipam_prefixes",
        "ipam_addresses",
        "ipam_services",
        "data_sources",
        "users",
        "extras_customization",
        "extras_templates",
        "extras_views",
        "extras_automation",
        "regions",
        "sites",
        "locations",
        "device_catalog",
        "extras_contexts",
        "racks",
        "module_catalog",
        "component_templates",
        "devices",
        "device_components",
        "virtualization_clusters",
        "virtualization_machines",
        "virtualization_components",
        "vpn_crypto",
        "vpn_tunnels",
        "vpn_l2vpns",
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
        ("tenancy.ContactGroup", "contact_group"),
        ("tenancy.ContactRole", "contact_role"),
        ("tenancy.Contact", "contact"),
        ("tenancy.ContactAssignment", "contact_assignment"),
        ("ipam.ASNRange", "asn_range"),
        ("ipam.Aggregate", "aggregate"),
        ("ipam.RouteTarget", "route_target"),
        ("ipam.VRF", "vrf"),
        ("ipam.VLANGroup", "vlan_group"),
        ("ipam.VLAN", "vlan"),
        ("ipam.VLANTranslationPolicy", "vlan_translation_policy"),
        ("ipam.VLANTranslationRule", "vlan_translation_rule"),
        ("ipam.Prefix", "prefix"),
        ("ipam.IPRange", "ip_range"),
        ("ipam.FHRPGroup", "fhrp_group"),
        ("ipam.IPAddress", "ip_address"),
        ("ipam.FHRPGroupAssignment", "fhrp_group_assignment"),
        ("ipam.ServiceTemplate", "service_template"),
        ("ipam.Service", "service"),
        ("core.DataSource", "data_source"),
        ("users.ObjectPermission", "object_permission"),
        ("users.Group", "user_group"),
        ("users.User", "user"),
        ("extras.CustomFieldChoiceSet", "custom_field_choice_set"),
        ("extras.CustomField", "custom_field"),
        ("extras.CustomLink", "custom_link"),
        ("extras.ExportTemplate", "export_template"),
        ("extras.ConfigTemplate", "config_template"),
        ("extras.SavedFilter", "saved_filter"),
        ("extras.TableConfig", "table_config"),
        ("extras.Webhook", "webhook"),
        ("extras.NotificationGroup", "notification_group"),
        ("extras.EventRule", "event_rule"),
        ("dcim.Region", "region"),
        ("dcim.Site", "site"),
        ("dcim.Location", "location"),
        ("dcim.Manufacturer", "manufacturer"),
        ("dcim.DeviceRole", "device_role"),
        ("dcim.Platform", "platform"),
        ("dcim.DeviceType", "device_type"),
        ("extras.ConfigContextProfile", "config_context_profile"),
        ("extras.ConfigContext", "config_context"),
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
        ("virtualization.ClusterType", "cluster_type"),
        ("virtualization.ClusterGroup", "cluster_group"),
        ("virtualization.Cluster", "cluster"),
        ("virtualization.VirtualMachineType", "virtual_machine_type"),
        ("virtualization.VirtualMachine", "virtual_machine"),
        ("virtualization.VMInterface", "vm_interface"),
        ("virtualization.VirtualDisk", "virtual_disk"),
        ("vpn.IKEProposal", "ike_proposal"),
        ("vpn.IKEPolicy", "ike_policy"),
        ("vpn.IPSecProposal", "ipsec_proposal"),
        ("vpn.IPSecPolicy", "ipsec_policy"),
        ("vpn.IPSecProfile", "ipsec_profile"),
        ("vpn.TunnelGroup", "tunnel_group"),
        ("vpn.Tunnel", "tunnel"),
        ("vpn.TunnelTermination", "tunnel_termination"),
        ("vpn.L2VPN", "l2vpn"),
        ("vpn.L2VPNTermination", "l2vpn_termination"),
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
