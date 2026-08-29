from __future__ import annotations

from typing import Final

WIRELESS_RESOURCE_KINDS: Final = frozenset(
    {
        "wireless_lan_group",
        "wireless_lan",
        "wireless_link",
    }
)

WIRELESS_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "wireless_lan_group": ("name", "slug", "description", "comments"),
    # Pre-shared keys are credentials and remain destination-local.
    "wireless_lan": (
        "ssid",
        "status",
        "auth_type",
        "auth_cipher",
        "description",
        "comments",
    ),
    "wireless_link": (
        "ssid",
        "status",
        "auth_type",
        "auth_cipher",
        "distance",
        "distance_unit",
        "description",
        "comments",
    ),
}

WIRELESS_EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "wireless_lan": ("scope_type",),
}

WIRELESS_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "wireless_lan_group": {
        "parent": ("wireless_lan_group", "parent"),
        "owner": ("owner", "owner"),
    },
    "wireless_lan": {
        "group": ("wireless_lan_group", "group"),
        "vlan": ("vlan", "vlan"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "wireless_link": {
        "interface_a": ("interface", "interface_a"),
        "interface_b": ("interface", "interface_b"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
}

WIRELESS_TAGGED_KINDS: Final = WIRELESS_RESOURCE_KINDS

WIRELESS_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "wireless_link": frozenset({"interface_a", "interface_b"}),
}

WIRELESS_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "wireless_lan_group": frozenset({"parent"}),
    "wireless_lan": frozenset({"group", "vlan", "tenant"}),
    "wireless_link": frozenset({"interface_a", "interface_b"}),
}

WIRELESS_SCOPE_TARGET_KINDS: Final = frozenset({"region", "site_group", "site", "location"})


def wireless_relationship_target(resource_kind: str, name: str) -> str | None:
    if resource_kind == "wireless_lan" and name.startswith("scope_"):
        target = name.removeprefix("scope_")
        return target if target in WIRELESS_SCOPE_TARGET_KINDS else None
    return None


def is_wireless_identity_relationship(resource_kind: str, name: str) -> bool:
    return name in WIRELESS_IDENTITY_RELATIONSHIPS.get(resource_kind, frozenset()) or (
        resource_kind == "wireless_lan" and name.startswith("scope_")
    )
