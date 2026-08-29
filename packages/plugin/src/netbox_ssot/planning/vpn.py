from __future__ import annotations

from typing import Final

VPN_RESOURCE_KINDS: Final = frozenset(
    {
        "ike_proposal",
        "ike_policy",
        "ipsec_proposal",
        "ipsec_policy",
        "ipsec_profile",
        "tunnel_group",
        "tunnel",
        "tunnel_termination",
        "l2vpn",
        "l2vpn_termination",
    }
)

VPN_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "ike_proposal": (
        "name",
        "authentication_method",
        "encryption_algorithm",
        "authentication_algorithm",
        "group",
        "sa_lifetime",
        "description",
        "comments",
    ),
    # Pre-shared keys are credentials and remain destination-local.
    "ike_policy": ("name", "version", "mode", "description", "comments"),
    "ipsec_proposal": (
        "name",
        "encryption_algorithm",
        "authentication_algorithm",
        "sa_lifetime_seconds",
        "sa_lifetime_data",
        "description",
        "comments",
    ),
    "ipsec_policy": ("name", "pfs_group", "description", "comments"),
    "ipsec_profile": ("name", "mode", "description", "comments"),
    "tunnel_group": ("name", "slug", "description", "comments"),
    "tunnel": ("name", "status", "encapsulation", "tunnel_id", "description", "comments"),
    "tunnel_termination": ("role",),
    "l2vpn": ("name", "slug", "type", "status", "identifier", "description", "comments"),
    "l2vpn_termination": (),
}

VPN_EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "tunnel_termination": ("termination_type",),
    "l2vpn_termination": ("assigned_object_type",),
}

VPN_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "ike_proposal": {"owner": ("owner", "owner")},
    "ike_policy": {"owner": ("owner", "owner")},
    "ipsec_proposal": {"owner": ("owner", "owner")},
    "ipsec_policy": {"owner": ("owner", "owner")},
    "ipsec_profile": {
        "ike_policy": ("ike_policy", "ike_policy"),
        "ipsec_policy": ("ipsec_policy", "ipsec_policy"),
        "owner": ("owner", "owner"),
    },
    "tunnel_group": {"owner": ("owner", "owner")},
    "tunnel": {
        "group": ("tunnel_group", "group"),
        "ipsec_profile": ("ipsec_profile", "ipsec_profile"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "tunnel_termination": {
        "tunnel": ("tunnel", "tunnel"),
        "outside_ip": ("ip_address", "outside_ip"),
    },
    "l2vpn": {
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "l2vpn_termination": {"l2vpn": ("l2vpn", "l2vpn")},
}

VPN_TAGGED_KINDS: Final = VPN_RESOURCE_KINDS

VPN_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "ipsec_profile": frozenset({"ike_policy", "ipsec_policy"}),
    "tunnel_termination": frozenset({"tunnel"}),
    "l2vpn_termination": frozenset({"l2vpn"}),
}

VPN_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "ike_proposal": frozenset(),
    "ike_policy": frozenset(),
    "ipsec_proposal": frozenset(),
    "ipsec_policy": frozenset(),
    "ipsec_profile": frozenset(),
    "tunnel_group": frozenset(),
    "tunnel": frozenset(),
    "tunnel_termination": frozenset(),
    "l2vpn": frozenset(),
    "l2vpn_termination": frozenset(),
}

VPN_TUNNEL_TERMINATION_TARGET_KINDS: Final = frozenset({"interface", "vm_interface"})
VPN_L2_TERMINATION_TARGET_KINDS: Final = frozenset({"vlan", "interface", "vm_interface"})


def vpn_relationship_target(resource_kind: str, name: str) -> str | None:
    if resource_kind == "ike_policy" and name == "proposal":
        return "ike_proposal"
    if resource_kind == "ipsec_policy" and name == "proposal":
        return "ipsec_proposal"
    if resource_kind == "l2vpn" and name in {"import_target", "export_target"}:
        return "route_target"
    if resource_kind == "tunnel_termination" and name.startswith("termination_"):
        target = name.removeprefix("termination_")
        return target if target in VPN_TUNNEL_TERMINATION_TARGET_KINDS else None
    if resource_kind == "l2vpn_termination" and name.startswith("assigned_"):
        target = name.removeprefix("assigned_")
        return target if target in VPN_L2_TERMINATION_TARGET_KINDS else None
    return None


def is_vpn_multi_relationship(resource_kind: str, name: str) -> bool:
    return (resource_kind in {"ike_policy", "ipsec_policy"} and name == "proposal") or (
        resource_kind == "l2vpn" and name in {"import_target", "export_target"}
    )


def is_vpn_identity_relationship(resource_kind: str, name: str) -> bool:
    return (
        name in VPN_IDENTITY_RELATIONSHIPS.get(resource_kind, frozenset())
        or (resource_kind == "tunnel_termination" and name.startswith("termination_"))
        or (resource_kind == "l2vpn_termination" and name.startswith("assigned_"))
    )
