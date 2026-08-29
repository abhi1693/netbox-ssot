from __future__ import annotations

from typing import Final

DCIM_RESOURCE_KINDS: Final = frozenset(
    {
        "manufacturer",
        "device_role",
        "platform",
        "device_type",
        "rack_group",
        "rack_role",
        "rack_type",
        "rack",
        "rack_reservation",
        "module_type_profile",
        "module_type",
        "console_port_template",
        "console_server_port_template",
        "power_port_template",
        "power_outlet_template",
        "interface_template",
        "front_port_template",
        "rear_port_template",
        "module_bay_template",
        "device_bay_template",
        "inventory_item_template",
        "inventory_item_role",
        "virtual_chassis",
        "device",
        "virtual_device_context",
        "module",
        "console_port",
        "console_server_port",
        "power_port",
        "power_outlet",
        "interface",
        "front_port",
        "rear_port",
        "module_bay",
        "device_bay",
        "inventory_item",
        "mac_address",
        "power_panel",
        "power_feed",
        "cable_bundle",
        "cable",
    }
)

ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "tag": ("name", "slug", "color", "description", "weight"),
    "tenant_group": ("name", "slug", "description", "comments"),
    "tenant": ("name", "slug", "description", "comments"),
    "site_group": ("name", "slug", "description", "comments"),
    "rir": ("name", "slug", "is_private", "description", "comments"),
    "asn": ("asn", "description", "comments"),
    "region": ("name", "slug", "description", "comments"),
    "site": (
        "name",
        "slug",
        "status",
        "facility",
        "time_zone",
        "description",
        "physical_address",
        "shipping_address",
        "latitude",
        "longitude",
        "comments",
    ),
    "location": ("name", "slug", "status", "facility", "description", "comments"),
    "manufacturer": ("name", "slug", "description", "comments"),
    "device_role": ("name", "slug", "color", "vm_role", "description", "comments"),
    "platform": ("name", "slug", "description", "comments"),
    "device_type": (
        "model",
        "slug",
        "part_number",
        "u_height",
        "exclude_from_utilization",
        "is_full_depth",
        "subdevice_role",
        "airflow",
        "weight",
        "weight_unit",
        "description",
        "comments",
    ),
    "rack_group": ("name", "slug", "description", "comments"),
    "rack_role": ("name", "slug", "color", "description", "comments"),
    "rack_type": (
        "model",
        "slug",
        "form_factor",
        "width",
        "u_height",
        "starting_unit",
        "desc_units",
        "outer_width",
        "outer_height",
        "outer_depth",
        "outer_unit",
        "weight",
        "max_weight",
        "weight_unit",
        "mounting_depth",
        "description",
        "comments",
    ),
    "rack": (
        "name",
        "facility_id",
        "status",
        "serial",
        "asset_tag",
        "form_factor",
        "width",
        "u_height",
        "starting_unit",
        "desc_units",
        "outer_width",
        "outer_height",
        "outer_depth",
        "outer_unit",
        "mounting_depth",
        "airflow",
        "weight",
        "max_weight",
        "weight_unit",
        "description",
        "comments",
    ),
    "module_type_profile": ("name", "description", "schema", "comments"),
    "module_type": ("model", "part_number", "airflow", "weight", "weight_unit", "description", "comments"),
    "inventory_item_role": ("name", "slug", "color", "description", "comments"),
    "console_port_template": ("name", "label", "type", "description"),
    "console_server_port_template": ("name", "label", "type", "description"),
    "power_port_template": ("name", "label", "type", "maximum_draw", "allocated_draw", "description"),
    "power_outlet_template": ("name", "label", "type", "color", "feed_leg", "description"),
    "interface_template": (
        "name",
        "label",
        "type",
        "enabled",
        "mgmt_only",
        "description",
        "poe_mode",
        "poe_type",
        "rf_role",
    ),
    "front_port_template": ("name", "label", "type", "color", "positions", "description"),
    "rear_port_template": ("name", "label", "type", "color", "positions", "description"),
    "module_bay_template": ("name", "label", "position", "enabled", "description"),
    "device_bay_template": ("name", "label", "enabled", "description"),
    "inventory_item_template": ("name", "label", "part_id", "description"),
    "virtual_chassis": ("name", "domain", "description", "comments"),
    "device": (
        "name",
        "serial",
        "asset_tag",
        "position",
        "face",
        "status",
        "airflow",
        "vc_position",
        "vc_priority",
        "latitude",
        "longitude",
        "description",
        "comments",
        "local_context_data",
    ),
    "virtual_device_context": ("name", "identifier", "status", "description", "comments"),
    "module": ("status", "serial", "asset_tag", "description", "comments"),
    "console_port": ("name", "label", "type", "speed", "description", "mark_connected"),
    "console_server_port": ("name", "label", "type", "speed", "description", "mark_connected"),
    "power_port": (
        "name",
        "label",
        "type",
        "maximum_draw",
        "allocated_draw",
        "description",
        "mark_connected",
    ),
    "power_outlet": (
        "name",
        "label",
        "type",
        "status",
        "color",
        "feed_leg",
        "description",
        "mark_connected",
    ),
    "interface": (
        "name",
        "label",
        "type",
        "enabled",
        "mtu",
        "speed",
        "duplex",
        "wwn",
        "mgmt_only",
        "mode",
        "rf_role",
        "rf_channel",
        "rf_channel_frequency",
        "rf_channel_width",
        "tx_power",
        "poe_mode",
        "poe_type",
        "description",
        "mark_connected",
    ),
    "front_port": ("name", "label", "type", "color", "positions", "description", "mark_connected"),
    "rear_port": ("name", "label", "type", "color", "positions", "description", "mark_connected"),
    "module_bay": ("name", "label", "position", "enabled", "description"),
    "device_bay": ("name", "label", "enabled", "description"),
    "inventory_item": (
        "name",
        "label",
        "status",
        "part_id",
        "serial",
        "asset_tag",
        "discovered",
        "description",
    ),
    "mac_address": ("mac_address", "description", "comments"),
    "rack_reservation": ("units", "status", "description", "comments"),
    "power_panel": ("name", "description", "comments"),
    "power_feed": (
        "name",
        "status",
        "type",
        "supply",
        "phase",
        "voltage",
        "amperage",
        "max_utilization",
        "mark_connected",
        "description",
        "comments",
    ),
    "cable_bundle": ("name", "description", "comments"),
    "cable": ("type", "status", "profile", "label", "color", "length", "length_unit", "description", "comments"),
}

# Canonical attributes that are represented outside ordinary model fields.
# Keeping them beside ATTRIBUTE_FIELDS gives the typed DiffSync model compiler a
# complete, stable schema without teaching it about NetBox ORM implementation details.
EXTRA_ATTRIBUTE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "tag": ("object_types",),
    "module_type": ("attributes",),
    "inventory_item": ("component_type",),
    "inventory_item_template": ("component_type",),
    "mac_address": ("assigned_object_type",),
}

# relationship name -> (target resource kind, model attribute)
RELATIONSHIP_FIELDS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "tag": {},
    "tenant_group": {"parent": ("tenant_group", "parent"), "owner": ("owner", "owner")},
    "tenant": {"group": ("tenant_group", "group"), "owner": ("owner", "owner")},
    "site_group": {"parent": ("site_group", "parent"), "owner": ("owner", "owner")},
    "rir": {"owner": ("owner", "owner")},
    "asn": {
        "rir": ("rir", "rir"),
        "role": ("role", "role"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "region": {"parent": ("region", "parent"), "owner": ("owner", "owner")},
    "site": {
        "region": ("region", "region"),
        "group": ("site_group", "group"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "location": {
        "site": ("site", "site"),
        "parent": ("location", "parent"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "manufacturer": {"owner": ("owner", "owner")},
    "device_role": {
        "parent": ("device_role", "parent"),
        "config_template": ("config_template", "config_template"),
        "owner": ("owner", "owner"),
    },
    "platform": {
        "parent": ("platform", "parent"),
        "manufacturer": ("manufacturer", "manufacturer"),
        "config_template": ("config_template", "config_template"),
        "owner": ("owner", "owner"),
    },
    "device_type": {
        "manufacturer": ("manufacturer", "manufacturer"),
        "default_platform": ("platform", "default_platform"),
        "owner": ("owner", "owner"),
    },
    "rack_group": {"owner": ("owner", "owner")},
    "rack_role": {"owner": ("owner", "owner")},
    "rack_type": {"manufacturer": ("manufacturer", "manufacturer"), "owner": ("owner", "owner")},
    "rack": {
        "site": ("site", "site"),
        "location": ("location", "location"),
        "group": ("rack_group", "group"),
        "tenant": ("tenant", "tenant"),
        "role": ("rack_role", "role"),
        "rack_type": ("rack_type", "rack_type"),
        "owner": ("owner", "owner"),
    },
    "module_type_profile": {"owner": ("owner", "owner")},
    "module_type": {
        "profile": ("module_type_profile", "profile"),
        "manufacturer": ("manufacturer", "manufacturer"),
        "owner": ("owner", "owner"),
    },
    "inventory_item_role": {"owner": ("owner", "owner")},
    "console_port_template": {
        "device_type": ("device_type", "device_type"),
        "module_type": ("module_type", "module_type"),
    },
    "console_server_port_template": {
        "device_type": ("device_type", "device_type"),
        "module_type": ("module_type", "module_type"),
    },
    "power_port_template": {
        "device_type": ("device_type", "device_type"),
        "module_type": ("module_type", "module_type"),
    },
    "power_outlet_template": {
        "device_type": ("device_type", "device_type"),
        "module_type": ("module_type", "module_type"),
        "power_port": ("power_port_template", "power_port"),
    },
    "interface_template": {
        "device_type": ("device_type", "device_type"),
        "module_type": ("module_type", "module_type"),
        "bridge": ("interface_template", "bridge"),
    },
    "front_port_template": {
        "device_type": ("device_type", "device_type"),
        "module_type": ("module_type", "module_type"),
    },
    "rear_port_template": {
        "device_type": ("device_type", "device_type"),
        "module_type": ("module_type", "module_type"),
    },
    "module_bay_template": {
        "device_type": ("device_type", "device_type"),
        "module_type": ("module_type", "module_type"),
    },
    "device_bay_template": {"device_type": ("device_type", "device_type")},
    "inventory_item_template": {
        "device_type": ("device_type", "device_type"),
        "parent": ("inventory_item_template", "parent"),
        "role": ("inventory_item_role", "role"),
        "manufacturer": ("manufacturer", "manufacturer"),
    },
    "virtual_chassis": {"master": ("device", "master"), "owner": ("owner", "owner")},
    "device": {
        "device_type": ("device_type", "device_type"),
        "role": ("device_role", "role"),
        "tenant": ("tenant", "tenant"),
        "platform": ("platform", "platform"),
        "site": ("site", "site"),
        "location": ("location", "location"),
        "rack": ("rack", "rack"),
        "cluster": ("cluster", "cluster"),
        "virtual_chassis": ("virtual_chassis", "virtual_chassis"),
        "primary_ip4": ("ip_address", "primary_ip4"),
        "primary_ip6": ("ip_address", "primary_ip6"),
        "oob_ip": ("ip_address", "oob_ip"),
        "config_template": ("config_template", "config_template"),
        "owner": ("owner", "owner"),
    },
    "virtual_device_context": {
        "device": ("device", "device"),
        "tenant": ("tenant", "tenant"),
        "primary_ip4": ("ip_address", "primary_ip4"),
        "primary_ip6": ("ip_address", "primary_ip6"),
        "owner": ("owner", "owner"),
    },
    "module": {
        "device": ("device", "device"),
        "module_bay": ("module_bay", "module_bay"),
        "module_type": ("module_type", "module_type"),
        "owner": ("owner", "owner"),
    },
    "module_bay": {
        "device": ("device", "device"),
        "module": ("module", "module"),
        "owner": ("owner", "owner"),
    },
    "device_bay": {
        "device": ("device", "device"),
        "installed_device": ("device", "installed_device"),
        "owner": ("owner", "owner"),
    },
    "console_port": {"device": ("device", "device"), "module": ("module", "module"), "owner": ("owner", "owner")},
    "console_server_port": {
        "device": ("device", "device"),
        "module": ("module", "module"),
        "owner": ("owner", "owner"),
    },
    "power_port": {"device": ("device", "device"), "module": ("module", "module"), "owner": ("owner", "owner")},
    "power_outlet": {
        "device": ("device", "device"),
        "module": ("module", "module"),
        "power_port": ("power_port", "power_port"),
        "owner": ("owner", "owner"),
    },
    "interface": {
        "device": ("device", "device"),
        "module": ("module", "module"),
        "parent": ("interface", "parent"),
        "bridge": ("interface", "bridge"),
        "lag": ("interface", "lag"),
        "untagged_vlan": ("vlan", "untagged_vlan"),
        "qinq_svlan": ("vlan", "qinq_svlan"),
        "vlan_translation_policy": ("vlan_translation_policy", "vlan_translation_policy"),
        "vrf": ("vrf", "vrf"),
        "primary_mac_address": ("mac_address", "primary_mac_address"),
        "owner": ("owner", "owner"),
    },
    "front_port": {"device": ("device", "device"), "module": ("module", "module"), "owner": ("owner", "owner")},
    "rear_port": {"device": ("device", "device"), "module": ("module", "module"), "owner": ("owner", "owner")},
    "inventory_item": {
        "device": ("device", "device"),
        "parent": ("inventory_item", "parent"),
        "role": ("inventory_item_role", "role"),
        "manufacturer": ("manufacturer", "manufacturer"),
        "owner": ("owner", "owner"),
    },
    "mac_address": {"owner": ("owner", "owner")},
    "rack_reservation": {
        "rack": ("rack", "rack"),
        "user": ("user", "user"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "power_panel": {"site": ("site", "site"), "location": ("location", "location"), "owner": ("owner", "owner")},
    "power_feed": {
        "power_panel": ("power_panel", "power_panel"),
        "rack": ("rack", "rack"),
        "tenant": ("tenant", "tenant"),
        "owner": ("owner", "owner"),
    },
    "cable_bundle": {"owner": ("owner", "owner")},
    "cable": {
        "tenant": ("tenant", "tenant"),
        "bundle": ("cable_bundle", "bundle"),
        "owner": ("owner", "owner"),
    },
}

TAGGED_KINDS: Final = (
    frozenset(
        {
            "tenant_group",
            "tenant",
            "site_group",
            "rir",
            "asn",
            "region",
            "site",
            "location",
            "manufacturer",
            "device_role",
            "platform",
            "device_type",
            "rack_group",
            "rack_role",
            "rack_type",
            "rack",
        }
    )
    | (
        DCIM_RESOURCE_KINDS
        - {
            "console_port_template",
            "console_server_port_template",
            "power_port_template",
            "power_outlet_template",
            "interface_template",
            "front_port_template",
            "rear_port_template",
            "module_bay_template",
            "device_bay_template",
            "inventory_item_template",
        }
    )
)

REQUIRED_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "module_type": frozenset({"manufacturer"}),
    "device_bay_template": frozenset({"device_type"}),
    "inventory_item_template": frozenset({"device_type"}),
    "device": frozenset({"device_type", "role", "site"}),
    "virtual_device_context": frozenset({"device"}),
    "module": frozenset({"device", "module_bay", "module_type"}),
    "module_bay": frozenset({"device"}),
    "device_bay": frozenset({"device"}),
    "console_port": frozenset({"device"}),
    "console_server_port": frozenset({"device"}),
    "power_port": frozenset({"device"}),
    "power_outlet": frozenset({"device"}),
    "interface": frozenset({"device"}),
    "front_port": frozenset({"device"}),
    "rear_port": frozenset({"device"}),
    "inventory_item": frozenset({"device"}),
    "rack_reservation": frozenset({"rack", "user"}),
    "power_panel": frozenset({"site"}),
    "power_feed": frozenset({"power_panel"}),
}

IDENTITY_RELATIONSHIPS: Final[dict[str, frozenset[str]]] = {
    "region": frozenset({"parent"}),
    "site_group": frozenset({"parent"}),
    "tenant_group": frozenset({"parent"}),
    "device_role": frozenset({"parent"}),
    "tenant": frozenset({"group"}),
    "location": frozenset({"site", "parent"}),
    "platform": frozenset({"manufacturer"}),
    "device_type": frozenset({"manufacturer"}),
    "rack_type": frozenset({"manufacturer"}),
    "rack": frozenset({"site", "location"}),
    "module_type": frozenset({"manufacturer"}),
    **{
        kind: frozenset({"device_type", "module_type"})
        for kind in (
            "console_port_template",
            "console_server_port_template",
            "power_port_template",
            "power_outlet_template",
            "interface_template",
            "front_port_template",
            "rear_port_template",
            "module_bay_template",
        )
    },
    "device_bay_template": frozenset({"device_type"}),
    "inventory_item_template": frozenset({"device_type", "parent"}),
    "device": frozenset({"site", "tenant", "virtual_chassis", "rack"}),
    "virtual_device_context": frozenset({"device"}),
    "module": frozenset({"module_bay"}),
    **{
        kind: frozenset({"device"})
        for kind in (
            "module_bay",
            "device_bay",
            "console_port",
            "console_server_port",
            "power_port",
            "power_outlet",
            "interface",
            "front_port",
            "rear_port",
        )
    },
    "module_bay": frozenset({"device", "module"}),
    "inventory_item": frozenset({"device", "parent"}),
    "mac_address": frozenset(),
    "rack_reservation": frozenset({"rack", "user"}),
    "power_panel": frozenset({"site"}),
    "power_feed": frozenset({"power_panel"}),
    "cable": frozenset(),
}

COMPONENT_KINDS: Final = frozenset(
    {"console_port", "console_server_port", "power_port", "power_outlet", "interface", "front_port", "rear_port"}
)
COMPONENT_TEMPLATE_KINDS: Final = frozenset(f"{kind}_template" for kind in COMPONENT_KINDS)
CABLE_TERMINATION_KINDS: Final = COMPONENT_KINDS | {"power_feed"}
MAC_ADDRESS_ASSIGNMENT_KINDS: Final = frozenset({"interface", "vm_interface"})


def relationship_target(resource_kind: str, name: str) -> str | None:
    configured = RELATIONSHIP_FIELDS.get(resource_kind, {}).get(name)
    if configured:
        return configured[0]
    if name == "tag" and resource_kind in TAGGED_KINDS:
        return "tag"
    if resource_kind == "interface" and name == "vdc":
        return "virtual_device_context"
    if resource_kind == "interface" and name == "tagged_vlan":
        return "vlan"
    if resource_kind == "interface" and name == "wireless_lan":
        return "wireless_lan"
    if resource_kind in {"front_port", "front_port_template"} and name.startswith("mapping_"):
        return "rear_port_template" if resource_kind.endswith("_template") else "rear_port"
    if resource_kind in {"inventory_item", "inventory_item_template"} and name.startswith("component_"):
        target = name.removeprefix("component_")
        allowed = COMPONENT_TEMPLATE_KINDS if resource_kind.endswith("_template") else COMPONENT_KINDS
        return target if target in allowed else None
    if resource_kind == "mac_address" and name.startswith("assigned_"):
        target = name.removeprefix("assigned_")
        return target if target in MAC_ADDRESS_ASSIGNMENT_KINDS else None
    if resource_kind == "cable" and name.startswith(("termination_a_", "termination_b_")):
        target = name.split("_", 2)[2]
        return target if target in CABLE_TERMINATION_KINDS else None
    return None


def is_multi_relationship(resource_kind: str, name: str) -> bool:
    return (
        name == "tag"
        or (resource_kind == "interface" and name == "vdc")
        or (resource_kind == "interface" and name in {"tagged_vlan", "wireless_lan"})
        or (resource_kind == "cable" and name.startswith(("termination_a_", "termination_b_")))
    )


def is_identity_relationship(resource_kind: str, name: str) -> bool:
    if name in IDENTITY_RELATIONSHIPS.get(resource_kind, frozenset()):
        return True
    if resource_kind == "mac_address" and name.startswith("assigned_"):
        return True
    return resource_kind == "cable" and name.startswith(("termination_a_", "termination_b_"))
