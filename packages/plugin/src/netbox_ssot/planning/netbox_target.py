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
from core.models import DataSource
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
from django.db.models import Q
from extras.models import (
    ConfigContext,
    ConfigContextProfile,
    ConfigTemplate,
    CustomField,
    CustomFieldChoiceSet,
    CustomLink,
    EventRule,
    ExportTemplate,
    NotificationGroup,
    SavedFilter,
    TableConfig,
    Tag,
    Webhook,
)
from ipam.models import (
    ASN,
    RIR,
    VLAN,
    VRF,
    Aggregate,
    ASNRange,
    FHRPGroup,
    FHRPGroupAssignment,
    IPAddress,
    IPRange,
    Prefix,
    Role,
    RouteTarget,
    Service,
    ServiceTemplate,
    VLANGroup,
    VLANTranslationPolicy,
    VLANTranslationRule,
)
from tenancy.models import Contact, ContactAssignment, ContactGroup, ContactRole, Tenant, TenantGroup
from users.models import Group, ObjectPermission, Owner, OwnerGroup, User
from virtualization.models import (
    Cluster,
    ClusterGroup,
    ClusterType,
    VirtualDisk,
    VirtualMachine,
    VirtualMachineType,
    VMInterface,
)
from vpn.models import (
    L2VPN,
    IKEPolicy,
    IKEProposal,
    IPSecPolicy,
    IPSecProfile,
    IPSecProposal,
    L2VPNTermination,
    Tunnel,
    TunnelGroup,
    TunnelTermination,
)
from wireless.models import WirelessLAN, WirelessLANGroup, WirelessLink

from .comparison import CanonicalRecord, natural_identity, normalize_value
from .core import portable_data_source_parameters
from .resource_registry import ATTRIBUTE_FIELDS, RELATIONSHIP_FIELDS, TAGGED_KINDS
from .tenancy import TENANCY_CONTACT_TARGET_KINDS
from .virtualization import VIRTUALIZATION_SCOPE_TARGET_KINDS
from .wireless import WIRELESS_SCOPE_TARGET_KINDS

MODEL_BY_KIND = {
    "tag": Tag,
    "owner_group": OwnerGroup,
    "owner": Owner,
    "data_source": DataSource,
    "custom_field_choice_set": CustomFieldChoiceSet,
    "custom_field": CustomField,
    "custom_link": CustomLink,
    "export_template": ExportTemplate,
    "saved_filter": SavedFilter,
    "table_config": TableConfig,
    "config_context_profile": ConfigContextProfile,
    "config_context": ConfigContext,
    "config_template": ConfigTemplate,
    "webhook": Webhook,
    "notification_group": NotificationGroup,
    "event_rule": EventRule,
    "object_permission": ObjectPermission,
    "user_group": Group,
    "user": User,
    "tenant_group": TenantGroup,
    "tenant": Tenant,
    "contact_group": ContactGroup,
    "contact_role": ContactRole,
    "contact": Contact,
    "contact_assignment": ContactAssignment,
    "cluster_type": ClusterType,
    "cluster_group": ClusterGroup,
    "cluster": Cluster,
    "virtual_machine_type": VirtualMachineType,
    "virtual_machine": VirtualMachine,
    "vm_interface": VMInterface,
    "virtual_disk": VirtualDisk,
    "ike_proposal": IKEProposal,
    "ike_policy": IKEPolicy,
    "ipsec_proposal": IPSecProposal,
    "ipsec_policy": IPSecPolicy,
    "ipsec_profile": IPSecProfile,
    "tunnel_group": TunnelGroup,
    "tunnel": Tunnel,
    "tunnel_termination": TunnelTermination,
    "l2vpn": L2VPN,
    "l2vpn_termination": L2VPNTermination,
    "wireless_lan_group": WirelessLANGroup,
    "wireless_lan": WirelessLAN,
    "wireless_link": WirelessLink,
    "site_group": SiteGroup,
    "rir": RIR,
    "role": Role,
    "asn": ASN,
    "asn_range": ASNRange,
    "route_target": RouteTarget,
    "vrf": VRF,
    "aggregate": Aggregate,
    "vlan_group": VLANGroup,
    "vlan": VLAN,
    "vlan_translation_policy": VLANTranslationPolicy,
    "vlan_translation_rule": VLANTranslationRule,
    "prefix": Prefix,
    "ip_range": IPRange,
    "fhrp_group": FHRPGroup,
    "ip_address": IPAddress,
    "fhrp_group_assignment": FHRPGroupAssignment,
    "service_template": ServiceTemplate,
    "service": Service,
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
    if resource_kind == "event_rule":
        queryset = queryset.filter(action_type__in=("webhook", "notification"))
    elif resource_kind in {"vlan_group", "prefix"}:
        supported = ("region", "sitegroup", "site", "location")
        scope_filter = Q(scope_type__isnull=True) | Q(scope_type__app_label="dcim", scope_type__model__in=supported)
        if resource_kind == "vlan_group":
            supported += ("rackgroup", "rack", "clustergroup", "cluster")
            scope_filter = (
                Q(scope_type__isnull=True)
                | Q(scope_type__app_label="dcim", scope_type__model__in=supported)
                | Q(
                    scope_type__app_label="virtualization",
                    scope_type__model__in=("clustergroup", "cluster"),
                )
            )
        queryset = queryset.filter(scope_filter)
    elif resource_kind == "ip_address":
        queryset = queryset.filter(
            Q(assigned_object_type__isnull=True)
            | Q(assigned_object_type__app_label="dcim", assigned_object_type__model="interface")
            | Q(assigned_object_type__app_label="virtualization", assigned_object_type__model="vminterface")
            | Q(assigned_object_type__app_label="ipam", assigned_object_type__model="fhrpgroup")
        )
    elif resource_kind == "fhrp_group_assignment":
        queryset = queryset.filter(
            Q(interface_type__app_label="dcim", interface_type__model="interface")
            | Q(interface_type__app_label="virtualization", interface_type__model="vminterface")
        )
    elif resource_kind == "service":
        queryset = queryset.filter(
            Q(parent_object_type__app_label="dcim", parent_object_type__model="device")
            | Q(parent_object_type__app_label="virtualization", parent_object_type__model="virtualmachine")
            | Q(parent_object_type__app_label="ipam", parent_object_type__model="fhrpgroup")
        )
    elif resource_kind == "mac_address":
        queryset = queryset.filter(
            Q(assigned_object_type__isnull=True)
            | Q(assigned_object_type__app_label="dcim", assigned_object_type__model="interface")
            | Q(assigned_object_type__app_label="virtualization", assigned_object_type__model="vminterface")
        )
    elif resource_kind in {"cluster", "wireless_lan"}:
        queryset = queryset.filter(
            Q(scope_type__isnull=True)
            | Q(scope_type__app_label="dcim", scope_type__model__in=("region", "sitegroup", "site", "location"))
        )
    elif resource_kind == "tunnel_termination":
        queryset = queryset.filter(
            Q(termination_type__app_label="dcim", termination_type__model="interface")
            | Q(termination_type__app_label="virtualization", termination_type__model="vminterface")
        )
    elif resource_kind == "l2vpn_termination":
        queryset = queryset.filter(
            Q(assigned_object_type__app_label="dcim", assigned_object_type__model="interface")
            | Q(assigned_object_type__app_label="virtualization", assigned_object_type__model="vminterface")
            | Q(assigned_object_type__app_label="ipam", assigned_object_type__model="vlan")
        )
    elif resource_kind == "contact_assignment":
        supported = Q(pk__in=[])
        for target_kind in TENANCY_CONTACT_TARGET_KINDS:
            target_model = MODEL_BY_KIND[target_kind]
            supported |= Q(
                object_type__app_label=target_model._meta.app_label,
                object_type__model=target_model._meta.model_name,
            )
        queryset = queryset.filter(supported)
    if related:
        queryset = queryset.select_related(*sorted(related))
    extra_related = {
        "custom_field": ("related_object_type",),
        "table_config": ("object_type",),
        "event_rule": ("action_object_type",),
        "rack_reservation": ("user",),
        "inventory_item": ("component_type",),
        "inventory_item_template": ("component_type",),
        "mac_address": ("assigned_object_type",),
        "circuit_termination": ("termination_type",),
        "circuit_group_assignment": ("member_type",),
        "vlan_group": ("scope_type",),
        "prefix": ("scope_type",),
        "ip_address": ("assigned_object_type",),
        "fhrp_group_assignment": ("interface_type",),
        "service": ("parent_object_type",),
        "contact_assignment": ("object_type",),
        "cluster": ("scope_type",),
        "wireless_lan": ("scope_type",),
        "tunnel_termination": ("termination_type",),
        "l2vpn_termination": ("assigned_object_type",),
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
    if resource_kind in {"custom_field", "custom_link", "export_template", "saved_filter", "event_rule"}:
        prefetch.append("object_types")
    if resource_kind == "config_context":
        prefetch.extend(
            (
                "regions",
                "site_groups",
                "sites",
                "locations",
                "device_types",
                "roles",
                "platforms",
                "tenant_groups",
                "tenants",
                "cluster_types",
                "cluster_groups",
                "clusters",
            )
        )
    if resource_kind == "notification_group":
        prefetch.extend(("groups", "users"))
    if resource_kind == "user_group":
        prefetch.append("object_permissions")
    if resource_kind == "user":
        prefetch.extend(("groups", "object_permissions"))
    if resource_kind == "site":
        prefetch.append("asns")
    if resource_kind == "provider":
        prefetch.append("asns")
    if resource_kind == "vrf":
        prefetch.extend(("import_targets", "export_targets"))
    if resource_kind in {"ike_policy", "ipsec_policy"}:
        prefetch.append("proposals")
    if resource_kind == "l2vpn":
        prefetch.extend(("import_targets", "export_targets"))
    if resource_kind == "service":
        prefetch.extend(("parent", "ipaddresses"))
    if resource_kind == "contact":
        prefetch.append("groups")
    if resource_kind == "contact_assignment":
        prefetch.append("object")
    if resource_kind == "cluster":
        prefetch.append("scope")
    if resource_kind == "wireless_lan":
        prefetch.append("scope")
    if resource_kind == "vm_interface":
        prefetch.append("tagged_vlans")
    if resource_kind in {"vlan_group", "prefix"}:
        prefetch.append("scope")
    if resource_kind == "ip_address":
        prefetch.append("assigned_object")
    if resource_kind == "fhrp_group_assignment":
        prefetch.append("interface")
    if resource_kind == "tunnel_termination":
        prefetch.append("termination")
    if resource_kind == "l2vpn_termination":
        prefetch.append("assigned_object")
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
    elif resource_kind == "data_source":
        parameters = portable_data_source_parameters(obj.type, obj.parameters)
        if parameters:
            add("/parameters", parameters)
    elif resource_kind == "vlan_group":
        add(
            "/vid_ranges",
            [{"start": value.lower, "end": value.upper - 1} for value in obj.vid_ranges],
        )
        add(
            "/scope_type",
            f"{obj.scope_type.app_label}.{obj.scope_type.model}" if obj.scope_type else None,
        )
    elif resource_kind in {
        "prefix",
        "ip_address",
        "fhrp_group_assignment",
        "service",
        "contact_assignment",
        "cluster",
        "wireless_lan",
        "tunnel_termination",
        "l2vpn_termination",
    }:
        field_name = {
            "prefix": "scope_type",
            "ip_address": "assigned_object_type",
            "fhrp_group_assignment": "interface_type",
            "service": "parent_object_type",
            "contact_assignment": "object_type",
            "cluster": "scope_type",
            "wireless_lan": "scope_type",
            "tunnel_termination": "termination_type",
            "l2vpn_termination": "assigned_object_type",
        }[resource_kind]
        content_type = getattr(obj, field_name)
        add(f"/{field_name}", f"{content_type.app_label}.{content_type.model}" if content_type else None)
    elif resource_kind in {"custom_field", "custom_link", "export_template", "saved_filter", "event_rule"}:
        add("/object_types", sorted(f"{item.app_label}.{item.model}" for item in obj.object_types.all()))
        if resource_kind == "custom_field":
            add(
                "/related_object_type",
                f"{obj.related_object_type.app_label}.{obj.related_object_type.model}"
                if obj.related_object_type
                else None,
            )
    elif resource_kind == "table_config":
        add("/object_type", f"{obj.object_type.app_label}.{obj.object_type.model}")
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
    elif resource_kind == "config_context":
        from .extras import CONFIG_CONTEXT_MULTI_RELATIONSHIPS

        for name, target_kind in CONFIG_CONTEXT_MULTI_RELATIONSHIPS.items():
            add_many(name, target_kind, getattr(obj, f"{name}s").all())
    elif resource_kind == "notification_group":
        add_many("group", "user_group", obj.groups.all())
        add_many("user", "user", obj.users.all())
    elif resource_kind == "contact":
        add_many("group", "contact_group", obj.groups.all())
    elif resource_kind == "contact_assignment" and obj.object is not None:
        target_kind = _kind_for_model(obj.object)
        if target_kind in TENANCY_CONTACT_TARGET_KINDS:
            add(f"object_{target_kind}", target_kind, obj.object)
    elif resource_kind == "cluster" and obj.scope is not None:
        target_kind = _kind_for_model(obj.scope)
        if target_kind in VIRTUALIZATION_SCOPE_TARGET_KINDS:
            add(f"scope_{target_kind}", target_kind, obj.scope)
    elif resource_kind == "wireless_lan" and obj.scope is not None:
        target_kind = _kind_for_model(obj.scope)
        if target_kind in WIRELESS_SCOPE_TARGET_KINDS:
            add(f"scope_{target_kind}", target_kind, obj.scope)
    elif resource_kind == "vm_interface":
        add_many("tagged_vlan", "vlan", obj.tagged_vlans.all())
    elif resource_kind == "ike_policy":
        add_many("proposal", "ike_proposal", obj.proposals.all())
    elif resource_kind == "ipsec_policy":
        add_many("proposal", "ipsec_proposal", obj.proposals.all())
    elif resource_kind == "tunnel_termination" and obj.termination is not None:
        target_kind = _kind_for_model(obj.termination)
        if target_kind in {"interface", "vm_interface"}:
            add(f"termination_{target_kind}", target_kind, obj.termination)
    elif resource_kind == "l2vpn":
        add_many("import_target", "route_target", obj.import_targets.all())
        add_many("export_target", "route_target", obj.export_targets.all())
    elif resource_kind == "l2vpn_termination" and obj.assigned_object is not None:
        target_kind = _kind_for_model(obj.assigned_object)
        if target_kind in {"vlan", "interface", "vm_interface"}:
            add(f"assigned_{target_kind}", target_kind, obj.assigned_object)
    elif resource_kind == "vrf":
        add_many("import_target", "route_target", obj.import_targets.all())
        add_many("export_target", "route_target", obj.export_targets.all())
    elif resource_kind in {"vlan_group", "prefix"} and obj.scope is not None:
        target_kind = _kind_for_model(obj.scope)
        if target_kind:
            add(f"scope_{target_kind}", target_kind, obj.scope)
    elif resource_kind == "ip_address" and obj.assigned_object is not None:
        target_kind = _kind_for_model(obj.assigned_object)
        if target_kind in {"interface", "vm_interface", "fhrp_group"}:
            add(f"assigned_{target_kind}", target_kind, obj.assigned_object)
    elif resource_kind == "fhrp_group_assignment" and obj.interface is not None:
        target_kind = _kind_for_model(obj.interface)
        if target_kind in {"interface", "vm_interface"}:
            add(f"interface_{target_kind}", target_kind, obj.interface)
    elif resource_kind == "service" and obj.parent is not None:
        target_kind = _kind_for_model(obj.parent)
        if target_kind in {"device", "virtual_machine", "fhrp_group"}:
            add(f"parent_{target_kind}", target_kind, obj.parent)
        add_many("ip_address", "ip_address", obj.ipaddresses.all())
    elif resource_kind == "event_rule" and obj.action_object is not None:
        target_kind = _kind_for_model(obj.action_object)
        if target_kind in {"webhook", "notification_group"}:
            add(f"action_{target_kind}", target_kind, obj.action_object)
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
        if target_kind in {"interface", "vm_interface"}:
            add(f"assigned_{target_kind}", target_kind, obj.assigned_object)
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
        "contact_group",
        "contact_role",
        "cluster_type",
        "cluster_group",
        "virtual_machine_type",
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
        "role",
        "asn_range",
    }
    if resource_kind in slug_kinds:
        attributes["/slug"] = obj.slug
    elif resource_kind in {
        "owner_group",
        "owner",
        "object_permission",
        "user_group",
        "data_source",
        "custom_field_choice_set",
        "custom_field",
        "custom_link",
        "export_template",
        "config_context_profile",
        "config_context",
        "config_template",
        "webhook",
        "notification_group",
        "event_rule",
        "route_target",
        "vlan_translation_policy",
        "service_template",
        "contact",
        "ike_proposal",
        "ike_policy",
        "ipsec_proposal",
        "ipsec_policy",
        "ipsec_profile",
        "tunnel",
    }:
        attributes["/name"] = obj.name
    elif resource_kind == "saved_filter":
        attributes["/slug"] = obj.slug
    elif resource_kind == "table_config":
        attributes["/object_type"] = f"{obj.object_type.app_label}.{obj.object_type.model}"
        attributes["/table"] = obj.table
        attributes["/name"] = obj.name
        if obj.user:
            relationships["user"] = _target_identity("user", obj.user)
    elif resource_kind == "user":
        attributes["/username"] = obj.username
    elif resource_kind == "asn":
        attributes["/asn"] = obj.asn
    elif resource_kind == "vrf":
        attributes["/name"] = obj.name
        attributes["/rd"] = obj.rd
        if obj.tenant:
            relationships["tenant"] = _target_identity("tenant", obj.tenant)
    elif resource_kind == "aggregate":
        attributes["/prefix"] = str(obj.prefix)
    elif resource_kind == "vlan_group":
        attributes["/slug"] = obj.slug
        if obj.scope is not None:
            target_kind = _kind_for_model(obj.scope)
            if target_kind:
                relationships[f"scope_{target_kind}"] = _target_identity(target_kind, obj.scope)
                attributes["/scope_type"] = obj.scope_type.model
    elif resource_kind == "vlan":
        attributes["/vid"] = obj.vid
        attributes["/name"] = obj.name
        if obj.group:
            relationships["group"] = _target_identity("vlan_group", obj.group)
        elif obj.site:
            relationships["site"] = _target_identity("site", obj.site)
    elif resource_kind == "vlan_translation_rule":
        attributes["/local_vid"] = obj.local_vid
        relationships["policy"] = _target_identity("vlan_translation_policy", obj.policy)
    elif resource_kind == "prefix":
        attributes["/prefix"] = str(obj.prefix)
        if obj.vrf:
            relationships["vrf"] = _target_identity("vrf", obj.vrf)
    elif resource_kind == "ip_range":
        attributes["/start_address"] = str(obj.start_address)
        attributes["/end_address"] = str(obj.end_address)
        if obj.vrf:
            relationships["vrf"] = _target_identity("vrf", obj.vrf)
    elif resource_kind == "ip_address":
        attributes["/address"] = str(obj.address)
        if obj.role:
            attributes["/role"] = obj.role
        if obj.vrf:
            relationships["vrf"] = _target_identity("vrf", obj.vrf)
        if obj.assigned_object is not None:
            target_kind = _kind_for_model(obj.assigned_object)
            if target_kind in {"interface", "vm_interface", "fhrp_group"}:
                relationships[f"assigned_{target_kind}"] = _target_identity(target_kind, obj.assigned_object)
                attributes["/assigned_object_type"] = obj.assigned_object_type.model
    elif resource_kind == "fhrp_group":
        attributes["/protocol"] = obj.protocol
        attributes["/group_id"] = obj.group_id
        attributes["/name"] = obj.name
    elif resource_kind == "fhrp_group_assignment":
        relationships["group"] = _target_identity("fhrp_group", obj.group)
        if obj.interface is not None:
            target_kind = _kind_for_model(obj.interface)
            if target_kind in {"interface", "vm_interface"}:
                relationships[f"interface_{target_kind}"] = _target_identity(target_kind, obj.interface)
                attributes["/interface_type"] = obj.interface_type.model
    elif resource_kind == "service":
        attributes["/name"] = obj.name
        attributes["/protocol"] = obj.protocol
        attributes["/ports"] = list(obj.ports)
        if obj.parent is not None:
            target_kind = _kind_for_model(obj.parent)
            if target_kind in {"device", "virtual_machine", "fhrp_group"}:
                relationships[f"parent_{target_kind}"] = _target_identity(target_kind, obj.parent)
                attributes["/parent_object_type"] = obj.parent_object_type.model
    elif resource_kind == "contact_assignment":
        relationships["contact"] = _target_identity("contact", obj.contact)
        relationships["role"] = _target_identity("contact_role", obj.role)
        if obj.object is not None:
            target_kind = _kind_for_model(obj.object)
            if target_kind in TENANCY_CONTACT_TARGET_KINDS:
                relationships[f"object_{target_kind}"] = _target_identity(target_kind, obj.object)
                attributes["/object_type"] = obj.object_type.model
    elif resource_kind == "cluster":
        attributes["/name"] = obj.name
        if obj.group:
            relationships["group"] = _target_identity("cluster_group", obj.group)
        if obj.scope is not None:
            target_kind = _kind_for_model(obj.scope)
            if target_kind in VIRTUALIZATION_SCOPE_TARGET_KINDS:
                relationships[f"scope_{target_kind}"] = _target_identity(target_kind, obj.scope)
                attributes["/scope_type"] = obj.scope_type.model
    elif resource_kind == "virtual_machine":
        attributes["/name"] = obj.name
        for name, target_kind in (
            ("site", "site"),
            ("cluster", "cluster"),
            ("device", "device"),
            ("tenant", "tenant"),
        ):
            target = getattr(obj, name)
            if target is not None:
                relationships[name] = _target_identity(target_kind, target)
    elif resource_kind in {"vm_interface", "virtual_disk"}:
        attributes["/name"] = obj.name
        relationships["virtual_machine"] = _target_identity("virtual_machine", obj.virtual_machine)
    elif resource_kind in {"tunnel_group", "l2vpn"}:
        attributes["/slug"] = obj.slug
    elif resource_kind == "wireless_lan_group":
        attributes["/slug"] = obj.slug
        if obj.parent:
            relationships["parent"] = _target_identity("wireless_lan_group", obj.parent)
    elif resource_kind == "wireless_link":
        relationships["interface_a"] = _target_identity("interface", obj.interface_a)
        relationships["interface_b"] = _target_identity("interface", obj.interface_b)
    elif resource_kind == "tunnel_termination":
        if obj.termination is not None:
            target_kind = _kind_for_model(obj.termination)
            if target_kind in {"interface", "vm_interface"}:
                relationships[f"termination_{target_kind}"] = _target_identity(target_kind, obj.termination)
                attributes["/termination_type"] = obj.termination_type.model
    elif resource_kind == "l2vpn_termination" and obj.assigned_object is not None:
        target_kind = _kind_for_model(obj.assigned_object)
        if target_kind in {"vlan", "interface", "vm_interface"}:
            relationships[f"assigned_{target_kind}"] = _target_identity(target_kind, obj.assigned_object)
            attributes["/assigned_object_type"] = obj.assigned_object_type.model
    elif resource_kind == "wireless_lan":
        attributes["/ssid"] = obj.ssid
        if obj.group:
            relationships["group"] = _target_identity("wireless_lan_group", obj.group)
        if obj.tenant:
            relationships["tenant"] = _target_identity("tenant", obj.tenant)
        if obj.vlan:
            relationships["vlan"] = _target_identity("vlan", obj.vlan)
        if obj.scope is not None:
            target_kind = _kind_for_model(obj.scope)
            if target_kind in WIRELESS_SCOPE_TARGET_KINDS:
                relationships[f"scope_{target_kind}"] = _target_identity(target_kind, obj.scope)
                attributes["/scope_type"] = obj.scope_type.model

    if resource_kind in {"region", "site_group", "tenant_group", "device_role", "contact_group"} and obj.parent:
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
            if target_kind in {"interface", "vm_interface"}:
                relationships[f"assigned_{target_kind}"] = _target_identity(target_kind, obj.assigned_object)
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
        or attributes.get("/ssid")
        or attributes.get("/cid")
        or attributes.get("/account")
        or attributes.get("/model")
        or attributes.get("/asn")
        or attributes.get("/slug")
        or fallback
    )
