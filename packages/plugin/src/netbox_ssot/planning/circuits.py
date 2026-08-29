from __future__ import annotations

from typing import Final

CIRCUITS_RESOURCE_KINDS: Final = frozenset(
    {
        "provider",
        "provider_account",
        "provider_network",
        "circuit_type",
        "circuit_group",
        "circuit",
        "circuit_termination",
        "virtual_circuit_type",
        "virtual_circuit",
        "virtual_circuit_termination",
        "circuit_group_assignment",
    }
)

CIRCUITS_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "provider": ("name", "slug", "description", "comments"),
    "provider_account": ("account", "name", "description", "comments"),
    "provider_network": ("name", "service_id", "description", "comments"),
    "circuit_type": ("name", "slug", "color", "description", "comments"),
    "circuit_group": ("name", "slug", "description", "comments"),
    "circuit": (
        "cid",
        "status",
        "install_date",
        "termination_date",
        "commit_rate",
        "description",
        "distance",
        "distance_unit",
        "comments",
    ),
    "circuit_termination": (
        "term_side",
        "port_speed",
        "upstream_speed",
        "xconnect_id",
        "pp_info",
        "description",
        "mark_connected",
    ),
    "virtual_circuit_type": ("name", "slug", "color", "description", "comments"),
    "virtual_circuit": ("cid", "status", "description", "comments"),
    "virtual_circuit_termination": ("role", "description"),
    "circuit_group_assignment": ("priority",),
}

CIRCUITS_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "provider": {"owner": ("owner", "owner")},
    "provider_account": {
        "provider": ("provider", "provider"),
        "owner": ("owner", "owner"),
    },
    "provider_network": {
        "provider": ("provider", "provider"),
        "owner": ("owner", "owner"),
    },
    "circuit_type": {"owner": ("owner", "owner")},
    "circuit_group": {
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "circuit": {
        "provider": ("provider", "provider"),
        "provider_account": ("provider_account", "provider_account"),
        "type": ("circuit_type", "type"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "circuit_termination": {"circuit": ("circuit", "circuit")},
    "virtual_circuit_type": {"owner": ("owner", "owner")},
    "virtual_circuit": {
        "provider_network": ("provider_network", "provider_network"),
        "provider_account": ("provider_account", "provider_account"),
        "type": ("virtual_circuit_type", "type"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "virtual_circuit_termination": {
        "virtual_circuit": ("virtual_circuit", "virtual_circuit"),
        "interface": ("interface", "interface"),
    },
    "circuit_group_assignment": {"group": ("circuit_group", "group")},
}

CIRCUITS_TAGGED_KINDS: Final = CIRCUITS_RESOURCE_KINDS

CIRCUITS_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "provider_account": frozenset({"provider"}),
    "provider_network": frozenset({"provider"}),
    "circuit": frozenset({"provider", "type"}),
    "circuit_termination": frozenset({"circuit"}),
    "virtual_circuit": frozenset({"provider_network", "type"}),
    "virtual_circuit_termination": frozenset({"interface"}),
    "circuit_group_assignment": frozenset({"group"}),
}

CIRCUITS_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "provider": frozenset(),
    "provider_account": frozenset({"provider"}),
    "provider_network": frozenset({"provider"}),
    "circuit_type": frozenset(),
    "circuit_group": frozenset(),
    "circuit": frozenset({"provider"}),
    "circuit_termination": frozenset({"circuit"}),
    "virtual_circuit_type": frozenset(),
    "virtual_circuit": frozenset({"provider_network"}),
    "virtual_circuit_termination": frozenset({"virtual_circuit", "interface"}),
    "circuit_group_assignment": frozenset({"group"}),
}

CIRCUIT_TERMINATION_TARGET_KINDS: Final = frozenset(
    {"region", "site_group", "site", "location", "provider_network"}
)
CIRCUIT_GROUP_MEMBER_KINDS: Final = frozenset({"circuit", "virtual_circuit"})


def circuit_relationship_target(resource_kind: str, name: str) -> str | None:
    if resource_kind == "provider" and name == "asn":
        return "asn"
    if resource_kind == "circuit_termination" and name.startswith("termination_"):
        target = name.removeprefix("termination_")
        return target if target in CIRCUIT_TERMINATION_TARGET_KINDS else None
    if resource_kind == "circuit_group_assignment" and name.startswith("member_"):
        target = name.removeprefix("member_")
        return target if target in CIRCUIT_GROUP_MEMBER_KINDS else None
    if resource_kind == "cable" and name.startswith(("termination_a_", "termination_b_")):
        target = name.split("_", 2)[2]
        return target if target == "circuit_termination" else None
    return None


def is_circuit_identity_relationship(resource_kind: str, name: str) -> bool:
    if name in CIRCUITS_IDENTITY_RELATIONSHIPS.get(resource_kind, frozenset()):
        return True
    return resource_kind == "circuit_group_assignment" and name.startswith("member_")
