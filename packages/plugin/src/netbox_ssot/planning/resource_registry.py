from __future__ import annotations

from typing import Final

from . import circuits, core, dcim, extras, ipam, tenancy, users, virtualization, vpn, wireless

RESOURCE_KINDS: Final = (
    dcim.DCIM_RESOURCE_KINDS
    | circuits.CIRCUITS_RESOURCE_KINDS
    | users.USERS_RESOURCE_KINDS
    | core.CORE_RESOURCE_KINDS
    | extras.EXTRAS_RESOURCE_KINDS
    | ipam.IPAM_RESOURCE_KINDS
    | tenancy.TENANCY_RESOURCE_KINDS
    | virtualization.VIRTUALIZATION_RESOURCE_KINDS
    | vpn.VPN_RESOURCE_KINDS
    | wireless.WIRELESS_RESOURCE_KINDS
)
ATTRIBUTE_FIELDS: Final = {
    **dcim.ATTRIBUTE_FIELDS,
    **circuits.CIRCUITS_ATTRIBUTE_FIELDS,
    **users.USERS_ATTRIBUTE_FIELDS,
    **core.CORE_ATTRIBUTE_FIELDS,
    **extras.EXTRAS_ATTRIBUTE_FIELDS,
    **ipam.IPAM_ATTRIBUTE_FIELDS,
    **tenancy.TENANCY_ATTRIBUTE_FIELDS,
    **virtualization.VIRTUALIZATION_ATTRIBUTE_FIELDS,
    **vpn.VPN_ATTRIBUTE_FIELDS,
    **wireless.WIRELESS_ATTRIBUTE_FIELDS,
}
CUSTOM_FIELD_KINDS: Final = frozenset(
    {
        "aggregate", "asn", "asn_range", "cable", "cable_bundle", "circuit", "circuit_group",
        "circuit_termination", "circuit_type", "cluster", "cluster_group", "cluster_type", "console_port",
        "console_server_port", "contact", "contact_assignment", "contact_group", "contact_role", "data_source",
        "device", "device_bay", "device_role", "device_type", "event_rule", "fhrp_group", "front_port",
        "ike_policy", "ike_proposal", "interface", "inventory_item", "inventory_item_role", "ip_address",
        "ip_range", "ipsec_policy", "ipsec_profile", "ipsec_proposal", "l2vpn", "l2vpn_termination",
        "location", "mac_address", "manufacturer", "module", "module_bay", "module_type",
        "module_type_profile", "platform", "power_feed", "power_outlet", "power_panel", "power_port", "prefix",
        "provider", "provider_account", "provider_network", "rack", "rack_group", "rack_reservation",
        "rack_role", "rack_type", "rear_port", "region", "rir", "role", "route_target", "service",
        "service_template", "site", "site_group", "tenant", "tenant_group", "tunnel", "tunnel_group",
        "tunnel_termination", "virtual_chassis", "virtual_circuit", "virtual_circuit_termination",
        "virtual_circuit_type", "virtual_device_context", "virtual_disk", "virtual_machine",
        "virtual_machine_type", "vlan", "vlan_group", "vm_interface", "vrf", "webhook", "wireless_lan",
        "wireless_lan_group", "wireless_link",
    }
)

_BASE_EXTRA_ATTRIBUTE_FIELDS: Final = {
    **dcim.EXTRA_ATTRIBUTE_FIELDS,
    **users.USERS_EXTRA_ATTRIBUTE_FIELDS,
    **core.CORE_EXTRA_ATTRIBUTE_FIELDS,
    **extras.EXTRAS_EXTRA_ATTRIBUTE_FIELDS,
    **ipam.IPAM_EXTRA_ATTRIBUTE_FIELDS,
    **tenancy.TENANCY_EXTRA_ATTRIBUTE_FIELDS,
    **virtualization.VIRTUALIZATION_EXTRA_ATTRIBUTE_FIELDS,
    **vpn.VPN_EXTRA_ATTRIBUTE_FIELDS,
    **wireless.WIRELESS_EXTRA_ATTRIBUTE_FIELDS,
}
_PROJECTION_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "device": ("manage_primary_ip_selectors",),
    "virtual_device_context": ("manage_primary_ip_selectors",),
    "virtual_machine": ("manage_primary_ip_selectors",),
    "vm_interface": ("manage_primary_mac_selector",),
    "interface": ("manage_wireless_lans",),
}
EXTRA_ATTRIBUTE_FIELDS: Final = {
    kind: tuple(
        dict.fromkeys(
            (
                *_BASE_EXTRA_ATTRIBUTE_FIELDS.get(kind, ()),
                *(_PROJECTION_ATTRIBUTE_FIELDS.get(kind, ())),
                *(("custom_fields", "unsupported_custom_field_targets") if kind in CUSTOM_FIELD_KINDS else ()),
            )
        )
    )
    for kind in ATTRIBUTE_FIELDS
}
RELATIONSHIP_FIELDS: Final = {
    **dcim.RELATIONSHIP_FIELDS,
    **circuits.CIRCUITS_RELATIONSHIP_FIELDS,
    **users.USERS_RELATIONSHIP_FIELDS,
    **core.CORE_RELATIONSHIP_FIELDS,
    **extras.EXTRAS_RELATIONSHIP_FIELDS,
    **ipam.IPAM_RELATIONSHIP_FIELDS,
    **tenancy.TENANCY_RELATIONSHIP_FIELDS,
    **virtualization.VIRTUALIZATION_RELATIONSHIP_FIELDS,
    **vpn.VPN_RELATIONSHIP_FIELDS,
    **wireless.WIRELESS_RELATIONSHIP_FIELDS,
}
TAGGED_KINDS: Final = (
    dcim.TAGGED_KINDS
    | circuits.CIRCUITS_TAGGED_KINDS
    | extras.EXTRAS_TAGGED_KINDS
    | ipam.IPAM_TAGGED_KINDS
    | tenancy.TENANCY_TAGGED_KINDS
    | virtualization.VIRTUALIZATION_TAGGED_KINDS
    | vpn.VPN_TAGGED_KINDS
    | wireless.WIRELESS_TAGGED_KINDS
)
REQUIRED_RELATIONSHIPS: Final = {
    **dcim.REQUIRED_RELATIONSHIPS,
    **circuits.CIRCUITS_REQUIRED_RELATIONSHIPS,
    **users.USERS_REQUIRED_RELATIONSHIPS,
    **core.CORE_REQUIRED_RELATIONSHIPS,
    **extras.EXTRAS_REQUIRED_RELATIONSHIPS,
    **ipam.IPAM_REQUIRED_RELATIONSHIPS,
    **tenancy.TENANCY_REQUIRED_RELATIONSHIPS,
    **virtualization.VIRTUALIZATION_REQUIRED_RELATIONSHIPS,
    **vpn.VPN_REQUIRED_RELATIONSHIPS,
    **wireless.WIRELESS_REQUIRED_RELATIONSHIPS,
}
IDENTITY_RELATIONSHIPS: Final = {
    **dcim.IDENTITY_RELATIONSHIPS,
    **circuits.CIRCUITS_IDENTITY_RELATIONSHIPS,
    **users.USERS_IDENTITY_RELATIONSHIPS,
    **core.CORE_IDENTITY_RELATIONSHIPS,
    **extras.EXTRAS_IDENTITY_RELATIONSHIPS,
    **ipam.IPAM_IDENTITY_RELATIONSHIPS,
    **tenancy.TENANCY_IDENTITY_RELATIONSHIPS,
    **virtualization.VIRTUALIZATION_IDENTITY_RELATIONSHIPS,
    **vpn.VPN_IDENTITY_RELATIONSHIPS,
    **wireless.WIRELESS_IDENTITY_RELATIONSHIPS,
}


def relationship_target(resource_kind: str, name: str) -> str | None:
    configured = (
        dcim.relationship_target(resource_kind, name)
        or circuits.circuit_relationship_target(resource_kind, name)
        or users.user_relationship_target(resource_kind, name)
        or extras.extras_relationship_target(resource_kind, name)
        or ipam.ipam_relationship_target(resource_kind, name)
        or tenancy.tenancy_relationship_target(resource_kind, name)
        or virtualization.virtualization_relationship_target(resource_kind, name)
        or vpn.vpn_relationship_target(resource_kind, name)
        or wireless.wireless_relationship_target(resource_kind, name)
    )
    if configured is not None:
        return configured
    parsed = parse_custom_field_relationship(name)
    if resource_kind in CUSTOM_FIELD_KINDS and parsed is not None:
        return parsed[1]
    return None


def is_multi_relationship(resource_kind: str, name: str) -> bool:
    configured = (
        dcim.is_multi_relationship(resource_kind, name)
        or (resource_kind == "provider" and name == "asn")
        or users.is_user_multi_relationship(resource_kind, name)
        or extras.is_extras_multi_relationship(resource_kind, name)
        or ipam.is_ipam_multi_relationship(resource_kind, name)
        or tenancy.is_tenancy_multi_relationship(resource_kind, name)
        or virtualization.is_virtualization_multi_relationship(resource_kind, name)
        or vpn.is_vpn_multi_relationship(resource_kind, name)
    )
    if configured:
        return True
    parsed = parse_custom_field_relationship(name)
    return resource_kind in CUSTOM_FIELD_KINDS and parsed is not None and parsed[0]


def is_identity_relationship(resource_kind: str, name: str) -> bool:
    return (
        dcim.is_identity_relationship(resource_kind, name)
        or circuits.is_circuit_identity_relationship(resource_kind, name)
        or ipam.is_ipam_identity_relationship(resource_kind, name)
        or tenancy.is_tenancy_identity_relationship(resource_kind, name)
        or virtualization.is_virtualization_identity_relationship(resource_kind, name)
        or vpn.is_vpn_identity_relationship(resource_kind, name)
        or wireless.is_wireless_identity_relationship(resource_kind, name)
    )


def custom_field_relationship_name(field_name: str, target_kind: str, *, multi: bool) -> str:
    cardinality = "multi" if multi else "object"
    return f"custom_field_{cardinality}_{target_kind}_{field_name}"


def parse_custom_field_relationship(name: str) -> tuple[bool, str, str] | None:
    for multi, cardinality in ((False, "object"), (True, "multi")):
        for target_kind in sorted(ATTRIBUTE_FIELDS, key=len, reverse=True):
            prefix = f"custom_field_{cardinality}_{target_kind}_"
            if name.startswith(prefix) and (field_name := name.removeprefix(prefix)):
                return multi, target_kind, field_name
    return None
