from __future__ import annotations

from typing import Final

IPAM_RESOURCE_KINDS: Final = frozenset(
    {
        "role",
        "asn_range",
        "route_target",
        "vrf",
        "aggregate",
        "vlan_group",
        "vlan",
        "vlan_translation_policy",
        "vlan_translation_rule",
        "prefix",
        "ip_range",
        "ip_address",
        "fhrp_group",
        "fhrp_group_assignment",
        "service_template",
        "service",
    }
)

IPAM_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "role": ("name", "slug", "weight", "description", "comments"),
    "asn_range": ("name", "slug", "start", "end", "description", "comments"),
    "route_target": ("name", "description", "comments"),
    "vrf": ("name", "rd", "enforce_unique", "description", "comments"),
    "aggregate": ("prefix", "date_added", "description", "comments"),
    "vlan_group": ("name", "slug", "vid_ranges", "description", "comments"),
    "vlan": ("vid", "name", "status", "description", "qinq_role", "comments"),
    "vlan_translation_policy": ("name", "description", "comments"),
    "vlan_translation_rule": ("local_vid", "remote_vid", "description"),
    "prefix": (
        "prefix",
        "status",
        "is_pool",
        "mark_utilized",
        "description",
        "comments",
    ),
    "ip_range": (
        "start_address",
        "end_address",
        "status",
        "description",
        "comments",
        "mark_populated",
        "mark_utilized",
    ),
    "ip_address": ("address", "status", "role", "dns_name", "description", "comments"),
    # auth_key is deliberately destination-local secret material.
    "fhrp_group": ("group_id", "name", "protocol", "auth_type", "description", "comments"),
    "fhrp_group_assignment": ("priority",),
    "service_template": ("name", "protocol", "ports", "description", "comments"),
    "service": ("name", "protocol", "ports", "description", "comments"),
}

IPAM_EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "vlan_group": ("scope_type",),
    "prefix": ("scope_type",),
    "ip_address": ("assigned_object_type",),
    "fhrp_group_assignment": ("interface_type",),
    "service": ("parent_object_type",),
}

IPAM_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "role": {"owner": ("owner", "owner")},
    "asn_range": {
        "rir": ("rir", "rir"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "route_target": {"tenant": ("tenant", "tenant"), "owner": ("owner", "owner")},
    "vrf": {"tenant": ("tenant", "tenant"), "owner": ("owner", "owner")},
    "aggregate": {
        "rir": ("rir", "rir"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "vlan_group": {"tenant": ("tenant", "tenant"), "owner": ("owner", "owner")},
    "vlan": {
        "site": ("site", "site"),
        "group": ("vlan_group", "group"),
        "tenant": ("tenant", "tenant"),
        "role": ("role", "role"),
        "qinq_svlan": ("vlan", "qinq_svlan"),
        "owner": ("owner", "owner"),
    },
    "vlan_translation_policy": {"owner": ("owner", "owner")},
    "vlan_translation_rule": {"policy": ("vlan_translation_policy", "policy")},
    "prefix": {
        "vrf": ("vrf", "vrf"),
        "tenant": ("tenant", "tenant"),
        "vlan": ("vlan", "vlan"),
        "role": ("role", "role"),
        "owner": ("owner", "owner"),
    },
    "ip_range": {
        "vrf": ("vrf", "vrf"),
        "tenant": ("tenant", "tenant"),
        "role": ("role", "role"),
        "owner": ("owner", "owner"),
    },
    "ip_address": {
        "vrf": ("vrf", "vrf"),
        "tenant": ("tenant", "tenant"),
        "nat_inside": ("ip_address", "nat_inside"),
        "owner": ("owner", "owner"),
    },
    "fhrp_group": {"owner": ("owner", "owner")},
    "fhrp_group_assignment": {"group": ("fhrp_group", "group")},
    "service_template": {"owner": ("owner", "owner")},
    "service": {"owner": ("owner", "owner")},
}

IPAM_TAGGED_KINDS: Final = frozenset(
    kind
    for kind in IPAM_RESOURCE_KINDS
    if kind not in {"fhrp_group_assignment", "vlan_translation_rule", "vlan_translation_policy"}
)

IPAM_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "asn_range": frozenset({"rir"}),
    "aggregate": frozenset({"rir"}),
    "vlan_translation_rule": frozenset({"policy"}),
    "fhrp_group_assignment": frozenset({"group"}),
}

IPAM_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {kind: frozenset() for kind in IPAM_RESOURCE_KINDS}
IPAM_IDENTITY_RELATIONSHIPS.update(
    {
        "vrf": frozenset({"tenant"}),
        "vlan_group": frozenset(),
        "vlan": frozenset({"site", "group"}),
        "vlan_translation_rule": frozenset({"policy"}),
        "prefix": frozenset({"vrf"}),
        "ip_range": frozenset({"vrf"}),
        "ip_address": frozenset({"vrf"}),
        "fhrp_group_assignment": frozenset({"group"}),
    }
)

IPAM_PREFIX_SCOPE_TARGET_KINDS: Final = frozenset({"region", "site_group", "site", "location"})
IPAM_VLAN_SCOPE_TARGET_KINDS: Final = IPAM_PREFIX_SCOPE_TARGET_KINDS | frozenset(
    {"rack_group", "rack", "cluster_group", "cluster"}
)
IPAM_ASSIGNMENT_TARGET_KINDS: Final = frozenset({"interface", "vm_interface", "fhrp_group"})
IPAM_SERVICE_PARENT_KINDS: Final = frozenset({"device", "virtual_machine", "fhrp_group"})


def ipam_relationship_target(resource_kind: str, name: str) -> str | None:
    if resource_kind == "vrf" and name in {"import_target", "export_target"}:
        return "route_target"
    if resource_kind == "service" and name == "ip_address":
        return "ip_address"
    if resource_kind in {"vlan_group", "prefix"} and name.startswith("scope_"):
        target = name.removeprefix("scope_")
        allowed = IPAM_VLAN_SCOPE_TARGET_KINDS if resource_kind == "vlan_group" else IPAM_PREFIX_SCOPE_TARGET_KINDS
        return target if target in allowed else None
    if resource_kind == "ip_address" and name.startswith("assigned_"):
        target = name.removeprefix("assigned_")
        return target if target in IPAM_ASSIGNMENT_TARGET_KINDS else None
    if resource_kind == "fhrp_group_assignment" and name.startswith("interface_"):
        target = name.removeprefix("interface_")
        return target if target in {"interface", "vm_interface"} else None
    if resource_kind == "service" and name.startswith("parent_"):
        target = name.removeprefix("parent_")
        return target if target in IPAM_SERVICE_PARENT_KINDS else None
    return None


def is_ipam_multi_relationship(resource_kind: str, name: str) -> bool:
    return (resource_kind == "vrf" and name in {"import_target", "export_target"}) or (
        resource_kind == "service" and name == "ip_address"
    )


def is_ipam_identity_relationship(resource_kind: str, name: str) -> bool:
    if name in IPAM_IDENTITY_RELATIONSHIPS.get(resource_kind, frozenset()):
        return True
    return (
        (resource_kind in {"vlan_group", "prefix"} and name.startswith("scope_"))
        or (resource_kind == "ip_address" and name.startswith("assigned_"))
        or (resource_kind == "fhrp_group_assignment" and name.startswith("interface_"))
        or (resource_kind == "service" and name.startswith("parent_"))
    )
