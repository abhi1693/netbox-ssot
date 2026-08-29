from __future__ import annotations

from typing import Final

TENANCY_RESOURCE_KINDS: Final = frozenset(
    {
        "contact_group",
        "contact_role",
        "contact",
        "contact_assignment",
    }
)

TENANCY_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "contact_group": ("name", "slug", "description", "comments"),
    "contact_role": ("name", "slug", "description", "comments"),
    "contact": ("name", "title", "phone", "email", "address", "link", "description", "comments"),
    "contact_assignment": ("priority",),
}

TENANCY_EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "contact_assignment": ("object_type",),
}

TENANCY_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "contact_group": {
        "parent": ("contact_group", "parent"),
        "owner": ("owner", "owner"),
    },
    "contact_role": {"owner": ("owner", "owner")},
    "contact": {"owner": ("owner", "owner")},
    "contact_assignment": {
        "contact": ("contact", "contact"),
        "role": ("contact_role", "role"),
    },
}

TENANCY_TAGGED_KINDS: Final = TENANCY_RESOURCE_KINDS

TENANCY_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "contact_assignment": frozenset({"contact", "role"}),
}

TENANCY_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "contact_group": frozenset({"parent"}),
    "contact_role": frozenset(),
    "contact": frozenset(),
    "contact_assignment": frozenset({"contact", "role"}),
}

# ContactAssignment can point only at NetBox models which advertise the
# contacts feature and are already represented by this provider graph.
TENANCY_CONTACT_TARGET_KINDS: Final = frozenset(
    {
        "tenant",
        "region",
        "site_group",
        "site",
        "location",
        "manufacturer",
        "rack",
        "device",
        "power_panel",
        "provider",
        "provider_account",
        "circuit",
        "virtual_circuit",
        "asn",
        "aggregate",
        "prefix",
        "ip_range",
        "ip_address",
        "service",
        "cluster_group",
        "cluster",
        "virtual_machine",
    }
)


def tenancy_relationship_target(resource_kind: str, name: str) -> str | None:
    if resource_kind == "contact" and name == "group":
        return "contact_group"
    if resource_kind == "contact_assignment" and name.startswith("object_"):
        target = name.removeprefix("object_")
        return target if target in TENANCY_CONTACT_TARGET_KINDS else None
    return None


def is_tenancy_multi_relationship(resource_kind: str, name: str) -> bool:
    return resource_kind == "contact" and name == "group"


def is_tenancy_identity_relationship(resource_kind: str, name: str) -> bool:
    return name in TENANCY_IDENTITY_RELATIONSHIPS.get(resource_kind, frozenset()) or (
        resource_kind == "contact_assignment" and name.startswith("object_")
    )
