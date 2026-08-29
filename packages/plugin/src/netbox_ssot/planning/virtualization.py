from __future__ import annotations

from typing import Final

VIRTUALIZATION_RESOURCE_KINDS: Final = frozenset(
    {
        "cluster_type",
        "cluster_group",
        "cluster",
        "virtual_machine_type",
        "virtual_machine",
        "vm_interface",
        "virtual_disk",
    }
)

VIRTUALIZATION_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "cluster_type": ("name", "slug", "description", "comments"),
    "cluster_group": ("name", "slug", "description", "comments"),
    "cluster": ("name", "status", "description", "comments"),
    "virtual_machine_type": (
        "name",
        "slug",
        "default_vcpus",
        "default_memory",
        "description",
        "comments",
    ),
    # Primary IP selectors are applied after IPAM assignments exist. NetBox
    # treats disk as an aggregate when VirtualDisk rows exist; the writer lets
    # those rows update the aggregate after their own mutations.
    "virtual_machine": (
        "name",
        "status",
        "start_on_boot",
        "vcpus",
        "memory",
        "disk",
        "description",
        "serial",
        "comments",
        "local_context_data",
    ),
    # The primary MAC selector is applied after MACAddress assignments exist.
    "vm_interface": ("name", "enabled", "mtu", "description", "mode"),
    "virtual_disk": ("name", "description", "size"),
}

VIRTUALIZATION_EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "cluster": ("scope_type",),
}

VIRTUALIZATION_RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "cluster_type": {"owner": ("owner", "owner")},
    "cluster_group": {"owner": ("owner", "owner")},
    "cluster": {
        "type": ("cluster_type", "type"),
        "group": ("cluster_group", "group"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "virtual_machine_type": {
        "default_platform": ("platform", "default_platform"),
        "owner": ("owner", "owner"),
    },
    "virtual_machine": {
        "virtual_machine_type": ("virtual_machine_type", "virtual_machine_type"),
        "site": ("site", "site"),
        "cluster": ("cluster", "cluster"),
        "device": ("device", "device"),
        "tenant": ("tenant", "tenant"),
        "platform": ("platform", "platform"),
        "role": ("device_role", "role"),
        "config_template": ("config_template", "config_template"),
        "owner": ("owner", "owner"),
        "primary_ip4": ("ip_address", "primary_ip4"),
        "primary_ip6": ("ip_address", "primary_ip6"),
    },
    "vm_interface": {
        "virtual_machine": ("virtual_machine", "virtual_machine"),
        "parent": ("vm_interface", "parent"),
        "bridge": ("vm_interface", "bridge"),
        "untagged_vlan": ("vlan", "untagged_vlan"),
        "qinq_svlan": ("vlan", "qinq_svlan"),
        "vlan_translation_policy": ("vlan_translation_policy", "vlan_translation_policy"),
        "vrf": ("vrf", "vrf"),
        "primary_mac_address": ("mac_address", "primary_mac_address"),
        "owner": ("owner", "owner"),
    },
    "virtual_disk": {
        "virtual_machine": ("virtual_machine", "virtual_machine"),
        "owner": ("owner", "owner"),
    },
}

VIRTUALIZATION_TAGGED_KINDS: Final = VIRTUALIZATION_RESOURCE_KINDS

VIRTUALIZATION_REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "cluster": frozenset({"type"}),
    "vm_interface": frozenset({"virtual_machine"}),
    "virtual_disk": frozenset({"virtual_machine"}),
}

VIRTUALIZATION_IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "cluster_type": frozenset(),
    "cluster_group": frozenset(),
    "cluster": frozenset({"group"}),
    "virtual_machine_type": frozenset(),
    "virtual_machine": frozenset({"site", "cluster", "device", "tenant"}),
    "vm_interface": frozenset({"virtual_machine"}),
    "virtual_disk": frozenset({"virtual_machine"}),
}

VIRTUALIZATION_SCOPE_TARGET_KINDS: Final = frozenset({"region", "site_group", "site", "location"})


def virtualization_relationship_target(resource_kind: str, name: str) -> str | None:
    if resource_kind == "vm_interface" and name == "tagged_vlan":
        return "vlan"
    if resource_kind == "cluster" and name.startswith("scope_"):
        target = name.removeprefix("scope_")
        return target if target in VIRTUALIZATION_SCOPE_TARGET_KINDS else None
    return None


def is_virtualization_multi_relationship(resource_kind: str, name: str) -> bool:
    return resource_kind == "vm_interface" and name == "tagged_vlan"


def is_virtualization_identity_relationship(resource_kind: str, name: str) -> bool:
    return name in VIRTUALIZATION_IDENTITY_RELATIONSHIPS.get(resource_kind, frozenset()) or (
        resource_kind == "cluster" and name.startswith("scope_")
    )
