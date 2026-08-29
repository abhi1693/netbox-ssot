from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from circuits.models import (
    Circuit,
    CircuitGroup,
    CircuitGroupAssignment,
    CircuitTermination,
    CircuitType,
    Provider,
    ProviderAccount,
    ProviderNetwork,
    VirtualCircuit,
    VirtualCircuitTermination,
    VirtualCircuitType,
)
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
from users.models import Group, ObjectPermission, Owner, OwnerGroup, User

from .comparison import CanonicalRecord, natural_identity, normalize_value
from .resource_registry import ATTRIBUTE_FIELDS, RELATIONSHIP_FIELDS, TAGGED_KINDS

MODEL_BY_KIND = {
    "tag": Tag,
    "owner_group": OwnerGroup,
    "owner": Owner,
    "object_permission": ObjectPermission,
    "user_group": Group,
    "user": User,
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
    "provider": Provider,
    "provider_account": ProviderAccount,
    "provider_network": ProviderNetwork,
    "circuit_type": CircuitType,
    "circuit_group": CircuitGroup,
    "circuit": Circuit,
    "circuit_termination": CircuitTermination,
    "virtual_circuit_type": VirtualCircuitType,
    "virtual_circuit": VirtualCircuit,
    "virtual_circuit_termination": VirtualCircuitTermination,
    "circuit_group_assignment": CircuitGroupAssignment,
    "cable_bundle": CableBundle,
    "cable": Cable,
}


def load_netbox_target_records() -> list[CanonicalRecord]:
    records: list[CanonicalRecord] = []
    for resource_kind in MODEL_BY_KIND:
        records.extend(_records(resource_kind, _queryset(resource_kind)))
    return records


def _queryset(resource_kind: str) -> Iterable[Any]:
    model = MODEL_BY_KIND[resource_kind]
    related = {field_name for _, field_name in RELATIONSHIP_FIELDS.get(resource_kind, {}).values()}
    queryset = model.objects.all()
    if related:
        queryset = queryset.select_related(*sorted(related))
    extra_related = {
        "asn": ("role",),
        "device_role": ("config_template",),
        "platform": ("config_template",),
        "device": ("config_template",),
        "rack_reservation": ("user",),
        "inventory_item": ("component_type",),
        "inventory_item_template": ("component_type",),
        "mac_address": ("assigned_object_type",),
        "circuit_termination": ("termination_type",),
        "circuit_group_assignment": ("member_type",),
    }.get(resource_kind, ())
    if extra_related:
        queryset = queryset.select_related(*extra_related)
    prefetch: list[str] = []
    if resource_kind in TAGGED_KINDS:
        prefetch.append("tags")
    if resource_kind == "interface":
        prefetch.append("vdcs")
    if resource_kind == "tag":
        prefetch.append("object_types")
    if resource_kind == "object_permission":
        prefetch.append("object_types")
    if resource_kind == "user_group":
        prefetch.append("object_permissions")
    if resource_kind == "user":
        prefetch.extend(("groups", "object_permissions"))
    if resource_kind == "site":
        prefetch.append("asns")
    if resource_kind == "provider":
        prefetch.append("asns")
    if resource_kind in {"front_port", "front_port_template"}:
        prefetch.append("mappings__rear_port")
    if resource_kind == "cable":
        prefetch.append("terminations")
    if resource_kind == "circuit_termination":
        prefetch.append("termination")
    if resource_kind == "circuit_group_assignment":
        prefetch.append("member")
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

    for path in ATTRIBUTE_FIELDS[resource_kind]:
        add(f"/{path}", getattr(obj, path))

    if resource_kind == "tag":
        object_types = sorted(f"{item.app_label}.{item.model}" for item in obj.object_types.all())
        if object_types:
            add("/object_types", object_types)
    elif resource_kind == "object_permission":
        add("/actions", sorted(obj.actions))
        add("/object_types", sorted(f"{item.app_label}.{item.model}" for item in obj.object_types.all()))
    elif resource_kind == "asn":
        add("/role", obj.role.slug if obj.role else None)
    elif resource_kind in {"device_role", "platform", "device"}:
        add("/config_template", obj.config_template.name if obj.config_template else None)
    elif resource_kind == "module_type":
        add("/attributes", obj.attribute_data)
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

    for name, (target_kind, field_name) in RELATIONSHIP_FIELDS[resource_kind].items():
        add(name, target_kind, getattr(obj, field_name))
    if resource_kind in TAGGED_KINDS:
        add_many("tag", "tag", obj.tags.all())
    if resource_kind == "user_group":
        add_many("permission", "object_permission", obj.object_permissions.all())
    elif resource_kind == "user":
        add_many("group", "user_group", obj.groups.all())
        add_many("permission", "object_permission", obj.object_permissions.all())
    elif resource_kind == "site" or resource_kind == "provider":
        add_many("asn", "asn", obj.asns.all())
    elif resource_kind == "interface":
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
    elif resource_kind == "circuit_termination" and obj.termination is not None:
        target_kind = _kind_for_model(obj.termination)
        if target_kind:
            add(f"termination_{target_kind}", target_kind, obj.termination)
    elif resource_kind == "circuit_group_assignment" and obj.member is not None:
        target_kind = _kind_for_model(obj.member)
        if target_kind:
            add(f"member_{target_kind}", target_kind, obj.member)
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
    elif resource_kind in {"owner_group", "owner", "object_permission", "user_group"}:
        attributes["/name"] = obj.name
    elif resource_kind == "user":
        attributes["/username"] = obj.username
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
    elif resource_kind in {"provider", "circuit_type", "virtual_circuit_type", "circuit_group"}:
        attributes["/slug"] = obj.slug
    elif resource_kind == "provider_account":
        attributes["/account"] = obj.account
        relationships["provider"] = _target_identity("provider", obj.provider)
    elif resource_kind == "provider_network":
        attributes["/name"] = obj.name
        relationships["provider"] = _target_identity("provider", obj.provider)
    elif resource_kind == "circuit":
        attributes["/cid"] = obj.cid
        relationships["provider"] = _target_identity("provider", obj.provider)
    elif resource_kind == "circuit_termination":
        attributes["/term_side"] = obj.term_side
        relationships["circuit"] = _target_identity("circuit", obj.circuit)
        if obj.termination is not None:
            target_kind = _kind_for_model(obj.termination)
            if target_kind:
                relationships[f"termination_{target_kind}"] = _target_identity(target_kind, obj.termination)
    elif resource_kind == "virtual_circuit":
        attributes["/cid"] = obj.cid
        relationships["provider_network"] = _target_identity("provider_network", obj.provider_network)
    elif resource_kind == "virtual_circuit_termination":
        relationships["virtual_circuit"] = _target_identity("virtual_circuit", obj.virtual_circuit)
        relationships["interface"] = _target_identity("interface", obj.interface)
    elif resource_kind == "circuit_group_assignment":
        relationships["group"] = _target_identity("circuit_group", obj.group)
        if obj.member is not None:
            target_kind = _kind_for_model(obj.member)
            if target_kind:
                relationships[f"member_{target_kind}"] = _target_identity(target_kind, obj.member)
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
        or attributes.get("/username")
        or attributes.get("/cid")
        or attributes.get("/account")
        or attributes.get("/model")
        or attributes.get("/asn")
        or attributes.get("/slug")
        or fallback
    )
