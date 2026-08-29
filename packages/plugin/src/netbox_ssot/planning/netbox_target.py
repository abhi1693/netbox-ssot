from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from dcim.models import (
    Cable,
    CableBundle,
    ConsolePort,
    ConsolePortTemplate,
    ConsoleServerPort,
    ConsoleServerPortTemplate,
    Device,
    DeviceBay,
    DeviceBayTemplate,
    DeviceRole,
    DeviceType,
    FrontPort,
    FrontPortTemplate,
    Interface,
    InterfaceTemplate,
    InventoryItem,
    InventoryItemRole,
    InventoryItemTemplate,
    Location,
    MACAddress,
    Manufacturer,
    Module,
    ModuleBay,
    ModuleBayTemplate,
    ModuleType,
    ModuleTypeProfile,
    Platform,
    PowerFeed,
    PowerOutlet,
    PowerOutletTemplate,
    PowerPanel,
    PowerPort,
    PowerPortTemplate,
    Rack,
    RackGroup,
    RackReservation,
    RackRole,
    RackType,
    RearPort,
    RearPortTemplate,
    Region,
    Site,
    SiteGroup,
    VirtualChassis,
    VirtualDeviceContext,
)
from extras.models import Tag
from ipam.models import ASN, RIR
from tenancy.models import Tenant, TenantGroup
from users.models import Owner, OwnerGroup

from .comparison import CanonicalRecord, natural_identity, normalize_value
from .dcim import ATTRIBUTE_FIELDS, DCIM_RESOURCE_KINDS, RELATIONSHIP_FIELDS, TAGGED_KINDS

MODEL_BY_KIND = {
    "tag": Tag,
    "owner_group": OwnerGroup,
    "owner": Owner,
    "tenant_group": TenantGroup,
    "tenant": Tenant,
    "site_group": SiteGroup,
    "rir": RIR,
    "asn": ASN,
    "region": Region,
    "site": Site,
    "location": Location,
    "manufacturer": Manufacturer,
    "device_role": DeviceRole,
    "platform": Platform,
    "device_type": DeviceType,
    "rack_group": RackGroup,
    "rack_role": RackRole,
    "rack_type": RackType,
    "rack": Rack,
    "rack_reservation": RackReservation,
    "module_type_profile": ModuleTypeProfile,
    "module_type": ModuleType,
    "console_port_template": ConsolePortTemplate,
    "console_server_port_template": ConsoleServerPortTemplate,
    "power_port_template": PowerPortTemplate,
    "power_outlet_template": PowerOutletTemplate,
    "interface_template": InterfaceTemplate,
    "front_port_template": FrontPortTemplate,
    "rear_port_template": RearPortTemplate,
    "module_bay_template": ModuleBayTemplate,
    "device_bay_template": DeviceBayTemplate,
    "inventory_item_template": InventoryItemTemplate,
    "inventory_item_role": InventoryItemRole,
    "virtual_chassis": VirtualChassis,
    "device": Device,
    "virtual_device_context": VirtualDeviceContext,
    "module": Module,
    "console_port": ConsolePort,
    "console_server_port": ConsoleServerPort,
    "power_port": PowerPort,
    "power_outlet": PowerOutlet,
    "interface": Interface,
    "front_port": FrontPort,
    "rear_port": RearPort,
    "module_bay": ModuleBay,
    "device_bay": DeviceBay,
    "inventory_item": InventoryItem,
    "mac_address": MACAddress,
    "power_panel": PowerPanel,
    "power_feed": PowerFeed,
    "cable_bundle": CableBundle,
    "cable": Cable,
}


def load_netbox_target_records() -> list[CanonicalRecord]:
    records: list[CanonicalRecord] = []
    records.extend(_records("owner_group", OwnerGroup.objects.all()))
    records.extend(_records("owner", Owner.objects.select_related("group")))
    records.extend(_records("tag", Tag.objects.select_related("owner").prefetch_related("object_types")))
    records.extend(
        _records(
            "tenant_group",
            TenantGroup.objects.select_related("parent", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "tenant",
            Tenant.objects.select_related("group", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "site_group",
            SiteGroup.objects.select_related("parent", "owner").prefetch_related("tags"),
        )
    )
    records.extend(_records("rir", RIR.objects.select_related("owner").prefetch_related("tags")))
    records.extend(
        _records(
            "asn",
            ASN.objects.select_related("rir", "role", "tenant", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "region",
            Region.objects.select_related("parent", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "site",
            Site.objects.select_related("region", "group", "tenant", "owner").prefetch_related("asns", "tags"),
        )
    )
    records.extend(
        _records(
            "location",
            Location.objects.select_related("site", "parent", "tenant", "owner").prefetch_related("tags"),
        )
    )
    records.extend(_records("manufacturer", Manufacturer.objects.select_related("owner").prefetch_related("tags")))
    records.extend(
        _records(
            "device_role",
            DeviceRole.objects.select_related("parent", "config_template", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "platform",
            Platform.objects.select_related(
                "parent",
                "manufacturer",
                "config_template",
                "owner",
            ).prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "device_type",
            DeviceType.objects.select_related("manufacturer", "default_platform", "owner").prefetch_related("tags"),
        )
    )
    records.extend(_records("rack_group", RackGroup.objects.select_related("owner").prefetch_related("tags")))
    records.extend(_records("rack_role", RackRole.objects.select_related("owner").prefetch_related("tags")))
    records.extend(
        _records(
            "rack_type",
            RackType.objects.select_related("manufacturer", "owner").prefetch_related("tags"),
        )
    )
    records.extend(
        _records(
            "rack",
            Rack.objects.select_related(
                "site",
                "location",
                "group",
                "tenant",
                "role",
                "rack_type",
                "owner",
            ).prefetch_related("tags"),
        )
    )
    foundation_kinds = {
        "manufacturer",
        "device_role",
        "platform",
        "device_type",
        "rack_group",
        "rack_role",
        "rack_type",
        "rack",
    }
    for resource_kind in sorted(DCIM_RESOURCE_KINDS - foundation_kinds):
        records.extend(_records(resource_kind, _dcim_queryset(resource_kind)))
    return records


def _dcim_queryset(resource_kind: str) -> Iterable[Any]:
    model = MODEL_BY_KIND[resource_kind]
    related = {field_name for _, field_name in RELATIONSHIP_FIELDS.get(resource_kind, {}).values()}
    queryset = model.objects.all()
    if related:
        queryset = queryset.select_related(*sorted(related))
    prefetch: list[str] = []
    if resource_kind in TAGGED_KINDS:
        prefetch.append("tags")
    if resource_kind == "interface":
        prefetch.append("vdcs")
    if resource_kind in {"front_port", "front_port_template"}:
        prefetch.append("mappings__rear_port")
    if resource_kind == "cable":
        prefetch.append("terminations")
    return queryset.prefetch_related(*prefetch) if prefetch else queryset


def _records(resource_kind: str, objects: Iterable[Any]) -> list[CanonicalRecord]:
    records: list[CanonicalRecord] = []
    for obj in objects:
        attributes = _attributes(resource_kind, obj)
        relationships = _relationships(resource_kind, obj)
        identity_key = natural_identity(resource_kind, attributes, relationships)
        records.append(
            CanonicalRecord(
                resource_kind=resource_kind,
                identity_key=identity_key,
                display_name=_display_name(attributes, str(obj)),
                external_id="",
                attributes=attributes,
                relationships=relationships,
                target_object_type=obj._meta.label_lower,
                target_object_id=str(obj.pk),
            )
        )
    return records


def _attributes(resource_kind: str, obj: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {}

    def add(path: str, value: Any) -> None:
        normalized = normalize_value(value)
        if normalized is not None and normalized != "":
            attributes[path] = normalized

    if resource_kind == "tag":
        for path in ("name", "slug", "color", "description", "weight"):
            add(f"/{path}", getattr(obj, path))
        object_types = sorted(f"{item.app_label}.{item.model}" for item in obj.object_types.all())
        if object_types:
            add("/object_types", object_types)
    elif resource_kind in {"owner_group", "owner"}:
        for path in ("name", "description"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind in {"tenant_group", "tenant", "site_group"}:
        for path in ("name", "slug", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "rir":
        for path in ("name", "slug", "is_private", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "asn":
        for path in ("asn", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
        add("/role", obj.role.slug if obj.role else None)
    elif resource_kind == "region":
        for path in ("name", "slug", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "site":
        for path in (
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
        ):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "location":
        for path in ("name", "slug", "status", "facility", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind in {"manufacturer", "rack_group"}:
        for path in ("name", "slug", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "rack_role":
        for path in ("name", "slug", "color", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "device_role":
        for path in ("name", "slug", "color", "vm_role", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
        add("/config_template", obj.config_template.name if obj.config_template else None)
    elif resource_kind == "platform":
        for path in ("name", "slug", "description", "comments"):
            add(f"/{path}", getattr(obj, path))
        add("/config_template", obj.config_template.name if obj.config_template else None)
    elif resource_kind == "device_type":
        for path in (
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
        ):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "rack_type":
        for path in (
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
        ):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind == "rack":
        for path in (
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
        ):
            add(f"/{path}", getattr(obj, path))
    elif resource_kind in ATTRIBUTE_FIELDS:
        for path in ATTRIBUTE_FIELDS[resource_kind]:
            add(f"/{path}", getattr(obj, path))
        if resource_kind == "module_type":
            add("/attributes", obj.attribute_data)
        elif resource_kind == "device":
            add("/config_template", obj.config_template.name if obj.config_template else None)
        elif resource_kind == "rack_reservation":
            add("/user", obj.user.username)
        elif resource_kind in {"inventory_item", "inventory_item_template"}:
            add(
                "/component_type",
                f"{obj.component_type.app_label}.{obj.component_type.model}" if obj.component_type else None,
            )
        elif resource_kind == "mac_address":
            add(
                "/assigned_object_type",
                f"{obj.assigned_object_type.app_label}.{obj.assigned_object_type.model}"
                if obj.assigned_object_type
                else None,
            )
    return attributes


def _relationships(resource_kind: str, obj: Any) -> dict[str, Any]:
    relationships: dict[str, Any] = {}

    def add(name: str, target_kind: str, target: Any) -> None:
        if target is not None:
            relationships[name] = _target_identity(target_kind, target)

    def add_many(name: str, target_kind: str, targets: Iterable[Any]) -> None:
        identities = sorted(_target_identity(target_kind, target) for target in targets)
        if identities:
            relationships[name] = identities

    if resource_kind == "tag":
        add("owner", "owner", obj.owner)
    elif resource_kind == "owner":
        add("group", "owner_group", obj.group)
    elif resource_kind == "tenant_group":
        add("parent", "tenant_group", obj.parent)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "tenant":
        add("group", "tenant_group", obj.group)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "site_group":
        add("parent", "site_group", obj.parent)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "rir":
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "asn":
        add("rir", "rir", obj.rir)
        add("tenant", "tenant", obj.tenant)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "region":
        add("parent", "region", obj.parent)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "site":
        add("region", "region", obj.region)
        add("group", "site_group", obj.group)
        add("tenant", "tenant", obj.tenant)
        add("owner", "owner", obj.owner)
        add_many("asn", "asn", obj.asns.all())
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "location":
        add("site", "site", obj.site)
        add("parent", "location", obj.parent)
        add("tenant", "tenant", obj.tenant)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind in {"manufacturer", "rack_group", "rack_role"}:
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "device_role":
        add("parent", "device_role", obj.parent)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "platform":
        add("parent", "platform", obj.parent)
        add("manufacturer", "manufacturer", obj.manufacturer)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "device_type":
        add("manufacturer", "manufacturer", obj.manufacturer)
        add("default_platform", "platform", obj.default_platform)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "rack_type":
        add("manufacturer", "manufacturer", obj.manufacturer)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind == "rack":
        add("site", "site", obj.site)
        add("location", "location", obj.location)
        add("group", "rack_group", obj.group)
        add("tenant", "tenant", obj.tenant)
        add("role", "rack_role", obj.role)
        add("rack_type", "rack_type", obj.rack_type)
        add("owner", "owner", obj.owner)
        add_many("tag", "tag", obj.tags.all())
    elif resource_kind in DCIM_RESOURCE_KINDS:
        for name, (target_kind, field_name) in RELATIONSHIP_FIELDS.get(resource_kind, {}).items():
            add(name, target_kind, getattr(obj, field_name))
        if resource_kind in TAGGED_KINDS:
            add_many("tag", "tag", obj.tags.all())
        if resource_kind == "interface":
            add_many("vdc", "virtual_device_context", obj.vdcs.all())
        elif resource_kind in {"front_port", "front_port_template"}:
            target_kind = "rear_port_template" if resource_kind.endswith("_template") else "rear_port"
            for mapping in obj.mappings.all():
                name = f"mapping_{mapping.front_port_position}_{mapping.rear_port_position}"
                add(name, target_kind, mapping.rear_port)
        elif resource_kind in {"inventory_item", "inventory_item_template"} and obj.component is not None:
            target_kind = _kind_for_model(obj.component)
            if target_kind:
                add(f"component_{target_kind}", target_kind, obj.component)
        elif resource_kind == "mac_address" and obj.assigned_object is not None:
            target_kind = _kind_for_model(obj.assigned_object)
            if target_kind == "interface":
                add("assigned_interface", target_kind, obj.assigned_object)
        elif resource_kind == "cable":
            for termination in obj.terminations.all():
                target = termination.termination
                target_kind = _kind_for_model(target)
                if target_kind:
                    name = f"termination_{termination.cable_end.lower()}_{target_kind}"
                    relationships.setdefault(name, []).append(_target_identity(target_kind, target))
            for value in relationships.values():
                if isinstance(value, list):
                    value.sort()
    return relationships


def _kind_for_model(obj: Any) -> str | None:
    label = obj._meta.label_lower
    return next((kind for kind, model in MODEL_BY_KIND.items() if model._meta.label_lower == label), None)


def _target_identity(resource_kind: str, obj: Any) -> str:
    attributes: dict[str, Any] = {}
    relationships: dict[str, str] = {}
    slug_kinds = {
        "tag",
        "tenant_group",
        "tenant",
        "site_group",
        "rir",
        "region",
        "site",
        "location",
        "manufacturer",
        "device_role",
        "platform",
        "rack_group",
        "rack_role",
    }
    if resource_kind in slug_kinds:
        attributes["/slug"] = obj.slug
    elif resource_kind in {"owner_group", "owner"}:
        attributes["/name"] = obj.name
    elif resource_kind == "asn":
        attributes["/asn"] = obj.asn

    if resource_kind in {"region", "site_group", "tenant_group", "device_role"} and obj.parent:
        relationships["parent"] = _target_identity(resource_kind, obj.parent)
    elif resource_kind == "tenant" and obj.group:
        relationships["group"] = _target_identity("tenant_group", obj.group)
    elif resource_kind == "location":
        relationships["site"] = _target_identity("site", obj.site)
        if obj.parent:
            relationships["parent"] = _target_identity("location", obj.parent)
    elif resource_kind == "platform" and obj.manufacturer:
        relationships["manufacturer"] = _target_identity("manufacturer", obj.manufacturer)
    elif resource_kind in {"device_type", "rack_type"}:
        attributes["/model"] = obj.model
        relationships["manufacturer"] = _target_identity("manufacturer", obj.manufacturer)
    elif resource_kind == "rack":
        attributes["/name"] = obj.name
        relationships["site"] = _target_identity("site", obj.site)
        if obj.location:
            relationships["location"] = _target_identity("location", obj.location)
    elif resource_kind in {"module_type_profile", "virtual_chassis", "cable_bundle"}:
        attributes["/name"] = obj.name
    elif resource_kind == "inventory_item_role":
        attributes["/slug"] = obj.slug
    elif resource_kind == "module_type":
        attributes["/model"] = obj.model
        relationships["manufacturer"] = _target_identity("manufacturer", obj.manufacturer)
    elif resource_kind in {
        "console_port_template",
        "console_server_port_template",
        "power_port_template",
        "power_outlet_template",
        "interface_template",
        "front_port_template",
        "rear_port_template",
        "module_bay_template",
    }:
        attributes["/name"] = obj.name
        if obj.device_type:
            relationships["device_type"] = _target_identity("device_type", obj.device_type)
        if obj.module_type:
            relationships["module_type"] = _target_identity("module_type", obj.module_type)
    elif resource_kind == "device_bay_template":
        attributes["/name"] = obj.name
        relationships["device_type"] = _target_identity("device_type", obj.device_type)
    elif resource_kind == "inventory_item_template":
        attributes["/name"] = obj.name
        relationships["device_type"] = _target_identity("device_type", obj.device_type)
        if obj.parent:
            relationships["parent"] = _target_identity("inventory_item_template", obj.parent)
    elif resource_kind == "device":
        attributes["/name"] = obj.name
        attributes["/asset_tag"] = obj.asset_tag
        attributes["/vc_position"] = obj.vc_position
        attributes["/position"] = obj.position
        attributes["/face"] = obj.face
        relationships["site"] = _target_identity("site", obj.site)
        if obj.tenant:
            relationships["tenant"] = _target_identity("tenant", obj.tenant)
        if obj.virtual_chassis:
            relationships["virtual_chassis"] = _target_identity("virtual_chassis", obj.virtual_chassis)
        if obj.rack:
            relationships["rack"] = _target_identity("rack", obj.rack)
    elif resource_kind == "virtual_device_context":
        attributes["/name"] = obj.name
        relationships["device"] = _target_identity("device", obj.device)
    elif resource_kind == "module":
        relationships["module_bay"] = _target_identity("module_bay", obj.module_bay)
    elif resource_kind == "module_bay":
        attributes["/name"] = obj.name
        relationships["device"] = _target_identity("device", obj.device)
        if obj.module:
            relationships["module"] = _target_identity("module", obj.module)
    elif resource_kind in {
        "device_bay",
        "console_port",
        "console_server_port",
        "power_port",
        "power_outlet",
        "interface",
        "front_port",
        "rear_port",
    }:
        attributes["/name"] = obj.name
        relationships["device"] = _target_identity("device", obj.device)
    elif resource_kind == "inventory_item":
        attributes["/name"] = obj.name
        relationships["device"] = _target_identity("device", obj.device)
        if obj.parent:
            relationships["parent"] = _target_identity("inventory_item", obj.parent)
    elif resource_kind == "mac_address":
        attributes["/mac_address"] = str(obj.mac_address)
        if obj.assigned_object is not None:
            target_kind = _kind_for_model(obj.assigned_object)
            if target_kind == "interface":
                relationships["assigned_interface"] = _target_identity(target_kind, obj.assigned_object)
    elif resource_kind == "rack_reservation":
        attributes["/units"] = list(obj.units)
        attributes["/user"] = obj.user.username
        relationships["rack"] = _target_identity("rack", obj.rack)
    elif resource_kind == "power_panel":
        attributes["/name"] = obj.name
        relationships["site"] = _target_identity("site", obj.site)
    elif resource_kind == "power_feed":
        attributes["/name"] = obj.name
        relationships["power_panel"] = _target_identity("power_panel", obj.power_panel)
    elif resource_kind == "cable":
        for termination in obj.terminations.all():
            target = termination.termination
            target_kind = _kind_for_model(target)
            if target_kind:
                name = f"termination_{termination.cable_end.lower()}_{target_kind}"
                relationships.setdefault(name, []).append(_target_identity(target_kind, target))
        for value in relationships.values():
            if isinstance(value, list):
                value.sort()
    return natural_identity(resource_kind, attributes, relationships)


def _display_name(attributes: dict[str, Any], fallback: str) -> str:
    return str(
        attributes.get("/name")
        or attributes.get("/model")
        or attributes.get("/asn")
        or attributes.get("/slug")
        or fallback
    )
