from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .adapters import build_adapter_pair
from .diffsync_engine import ComparisonOnlyDiffSyncEngine
from .resource_registry import ATTRIBUTE_FIELDS, is_multi_relationship

ENGINE_VERSION = "1.1"
SUPPORTED_RESOURCE_KINDS = frozenset(ATTRIBUTE_FIELDS)
MULTI_RELATIONSHIPS = {
    "tenant_group": frozenset({"tag"}),
    "tenant": frozenset({"tag"}),
    "site_group": frozenset({"tag"}),
    "rir": frozenset({"tag"}),
    "asn": frozenset({"tag"}),
    "region": frozenset({"tag"}),
    "site": frozenset({"asn", "tag"}),
    "location": frozenset({"tag"}),
    "manufacturer": frozenset({"tag"}),
    "device_role": frozenset({"tag"}),
    "platform": frozenset({"tag"}),
    "device_type": frozenset({"tag"}),
    "rack_group": frozenset({"tag"}),
    "rack_role": frozenset({"tag"}),
    "rack_type": frozenset({"tag"}),
    "rack": frozenset({"tag"}),
}


class ComparisonAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    NO_CHANGE = "no_change"
    CONFLICT = "conflict"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    resource_kind: str
    identity_key: str
    display_name: str
    external_id: str
    attributes: dict[str, Any]
    relationships: dict[str, Any]
    target_object_type: str = ""
    target_object_id: str = ""

    @property
    def uid(self) -> str:
        return f"{self.resource_kind}:{self.identity_key}"

    @property
    def payload(self) -> dict[str, dict[str, Any]]:
        return {"attributes": self.attributes, "relationships": self.relationships}


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    resource_kind: str
    external_id: str
    display_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    action: ComparisonAction
    source: CanonicalRecord
    target: CanonicalRecord | None
    changes: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    match_basis: str = "exact_natural_key"
    reason: str = ""


def merge_observed_fields(source: CanonicalRecord, target: CanonicalRecord) -> CanonicalRecord:
    """Build a full desired record while preserving destination fields the provider did not observe."""
    if source.uid != target.uid:
        raise ValueError("Observed fields can be merged only for records with the same natural identity.")
    return CanonicalRecord(
        resource_kind=source.resource_kind,
        identity_key=source.identity_key,
        display_name=source.display_name,
        external_id=source.external_id,
        attributes={**target.attributes, **source.attributes},
        relationships={**target.relationships, **source.relationships},
        target_object_type=source.target_object_type,
        target_object_id=source.target_object_id,
    )


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple, set)):
        return [normalize_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def normalize_relationship_cardinality(
    resource_kind: str,
    relationship_values: dict[str, list[str]],
) -> dict[str, Any]:
    relationships: dict[str, Any] = {}
    for name, values in sorted(relationship_values.items()):
        if is_multi_relationship(resource_kind, name) or name in MULTI_RELATIONSHIPS.get(resource_kind, frozenset()):
            relationships[name] = sorted(values)
        elif len(values) == 1:
            relationships[name] = values[0]
        else:
            raise ValueError(f"Scalar relationship {name!r} contains {len(values)} targets.")
    return relationships


def natural_identity(
    resource_kind: str,
    attributes: dict[str, Any],
    relationships: dict[str, Any],
) -> str:
    unsupported_custom_fields = attributes.get("/unsupported_custom_field_targets")
    if unsupported_custom_fields:
        raise ValueError(
            "Custom fields reference objects outside the supported provider graph: "
            + ", ".join(str(value) for value in unsupported_custom_fields)
        )

    def required_attribute(name: str) -> Any:
        value = attributes.get(f"/{name}")
        if value is None or value == "":
            raise ValueError(f"Missing identity attribute /{name}.")
        return value

    def required_relationship(name: str) -> str:
        value = relationships.get(name)
        if not value:
            raise ValueError(f"Missing identity relationship {name}.")
        return value

    parts: list[Any]
    if resource_kind in {
        "region",
        "site_group",
        "tenant_group",
        "device_role",
        "contact_group",
        "wireless_lan_group",
    }:
        parts = [resource_kind, relationships.get("parent", "root"), required_attribute("slug")]
    elif resource_kind == "tenant":
        parts = [resource_kind, relationships.get("group", "root"), required_attribute("slug")]
    elif resource_kind in {"tag", "rir", "site", "manufacturer", "rack_group", "rack_role"}:
        parts = [resource_kind, "slug", required_attribute("slug")]
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
        "config_template",
        "webhook",
        "notification_group",
    }:
        parts = [resource_kind, "name", required_attribute("name")]
    elif resource_kind == "saved_filter":
        parts = [resource_kind, "slug", required_attribute("slug")]
    elif resource_kind == "table_config":
        parts = [
            resource_kind,
            required_attribute("object_type"),
            required_attribute("table"),
            required_attribute("name"),
            relationships.get("user", "shared"),
        ]
    elif resource_kind == "config_context":
        unsupported = attributes.get("/unsupported_assignment_types")
        if unsupported:
            raise ValueError(
                "The config context uses qualifiers outside the supported provider graph: "
                + ", ".join(str(value) for value in unsupported)
                + "."
            )
        parts = [resource_kind, "name", required_attribute("name")]
    elif resource_kind == "event_rule":
        action_type = required_attribute("action_type")
        if action_type not in {"webhook", "notification"}:
            raise ValueError(f"Event rule action type {action_type!r} is not portable.")
        expected = "action_webhook" if action_type == "webhook" else "action_notification_group"
        if not relationships.get(expected):
            raise ValueError(f"Event rule requires relationship {expected}.")
        parts = [resource_kind, "name", required_attribute("name")]
    elif resource_kind == "user":
        parts = [resource_kind, "username", str(required_attribute("username")).casefold()]
    elif resource_kind == "asn":
        parts = [resource_kind, "asn", required_attribute("asn")]
    elif resource_kind in {
        "role",
        "asn_range",
        "contact_role",
        "cluster_type",
        "cluster_group",
        "virtual_machine_type",
    }:
        parts = [resource_kind, "slug", required_attribute("slug")]
    elif resource_kind == "contact":
        parts = [resource_kind, "name", str(required_attribute("name")).casefold()]
    elif resource_kind == "contact_assignment":
        objects = sorted((name, value) for name, value in relationships.items() if name.startswith("object_"))
        if attributes.get("/object_type") and len(objects) != 1:
            raise ValueError("The contact assignment targets a model outside the supported provider graph.")
        if len(objects) != 1:
            raise ValueError("A contact assignment requires exactly one supported target object.")
        parts = [
            resource_kind,
            objects,
            required_relationship("contact"),
            required_relationship("role"),
        ]
    elif resource_kind == "cluster":
        scopes = sorted((name, value) for name, value in relationships.items() if name.startswith("scope_"))
        if attributes.get("/scope_type") and len(scopes) != 1:
            raise ValueError("The cluster scope targets a model outside the supported DCIM graph.")
        parts = [
            resource_kind,
            relationships.get("group", "no-group"),
            scopes or "global",
            str(required_attribute("name")).casefold(),
        ]
    elif resource_kind == "virtual_machine":
        placement = relationships.get("cluster") or relationships.get("device") or relationships.get("site")
        if not isinstance(placement, str) or not placement:
            raise ValueError("A virtual machine requires a cluster, device, or site for portable identity.")
        parts = [
            resource_kind,
            placement,
            relationships.get("tenant", "no-tenant"),
            str(required_attribute("name")).casefold(),
        ]
    elif resource_kind in {"vm_interface", "virtual_disk"}:
        parts = [resource_kind, required_relationship("virtual_machine"), required_attribute("name")]
    elif resource_kind in {
        "ike_proposal",
        "ike_policy",
        "ipsec_proposal",
        "ipsec_policy",
        "ipsec_profile",
        "tunnel",
    }:
        parts = [resource_kind, "name", str(required_attribute("name")).casefold()]
    elif resource_kind == "tunnel_group":
        parts = [resource_kind, "slug", required_attribute("slug")]
    elif resource_kind == "tunnel_termination":
        terminations = sorted((name, value) for name, value in relationships.items() if name.startswith("termination_"))
        if attributes.get("/termination_type") and len(terminations) != 1:
            raise ValueError("The tunnel termination targets an interface outside the supported provider graph.")
        if len(terminations) != 1:
            raise ValueError("A tunnel termination requires exactly one supported interface.")
        parts = [resource_kind, terminations]
    elif resource_kind == "l2vpn":
        parts = [resource_kind, "slug", required_attribute("slug")]
    elif resource_kind == "l2vpn_termination":
        assignments = sorted((name, value) for name, value in relationships.items() if name.startswith("assigned_"))
        if attributes.get("/assigned_object_type") and len(assignments) != 1:
            raise ValueError("The L2VPN termination targets an object outside the supported provider graph.")
        if len(assignments) != 1:
            raise ValueError("An L2VPN termination requires exactly one supported assigned object.")
        parts = [resource_kind, assignments]
    elif resource_kind == "wireless_lan":
        scopes = sorted((name, value) for name, value in relationships.items() if name.startswith("scope_"))
        if attributes.get("/scope_type") and len(scopes) != 1:
            raise ValueError("The wireless LAN scope targets a model outside the supported DCIM graph.")
        if len(scopes) > 1:
            raise ValueError("A wireless LAN can contain at most one scope.")
        parts = [
            resource_kind,
            relationships.get("group", "no-group"),
            scopes or "global",
            relationships.get("tenant", "no-tenant"),
            relationships.get("vlan", "no-vlan"),
            required_attribute("ssid"),
        ]
    elif resource_kind == "wireless_link":
        endpoints = sorted((required_relationship("interface_a"), required_relationship("interface_b")))
        if endpoints[0] == endpoints[1]:
            raise ValueError("A wireless link requires two distinct interfaces.")
        parts = [resource_kind, endpoints]
    elif resource_kind in {"route_target", "vlan_translation_policy", "service_template"}:
        parts = [resource_kind, "name", required_attribute("name")]
    elif resource_kind == "vrf":
        rd = attributes.get("/rd")
        parts = (
            [resource_kind, "rd", rd]
            if rd
            else [resource_kind, relationships.get("tenant", "global"), "name", required_attribute("name")]
        )
    elif resource_kind == "aggregate":
        parts = [resource_kind, "prefix", required_attribute("prefix")]
    elif resource_kind == "vlan_group":
        scope = sorted((name, value) for name, value in relationships.items() if name.startswith("scope_"))
        if attributes.get("/scope_type") and len(scope) != 1:
            raise ValueError("The VLAN group scope targets a model outside the supported IPAM dependency graph.")
        parts = [resource_kind, scope or "global", required_attribute("slug")]
    elif resource_kind == "vlan":
        container = relationships.get("group") or relationships.get("site") or "global"
        parts = [resource_kind, container, required_attribute("vid"), required_attribute("name")]
    elif resource_kind == "vlan_translation_rule":
        parts = [resource_kind, required_relationship("policy"), required_attribute("local_vid")]
    elif resource_kind == "prefix":
        scope = sorted((name, value) for name, value in relationships.items() if name.startswith("scope_"))
        if attributes.get("/scope_type") and len(scope) != 1:
            raise ValueError("The prefix scope targets a model outside the supported IPAM dependency graph.")
        parts = [resource_kind, relationships.get("vrf", "global"), required_attribute("prefix")]
    elif resource_kind == "ip_range":
        parts = [
            resource_kind,
            relationships.get("vrf", "global"),
            required_attribute("start_address"),
            required_attribute("end_address"),
        ]
    elif resource_kind == "ip_address":
        assigned = sorted((name, value) for name, value in relationships.items() if name.startswith("assigned_"))
        if attributes.get("/assigned_object_type") and len(assigned) != 1:
            raise ValueError("The IP address assignment targets a model outside the supported provider graph.")
        parts = [
            resource_kind,
            relationships.get("vrf", "global"),
            required_attribute("address"),
            attributes.get("/role", "no-role"),
            assigned or "unassigned",
        ]
    elif resource_kind == "fhrp_group":
        parts = [
            resource_kind,
            required_attribute("protocol"),
            required_attribute("group_id"),
            attributes.get("/name", ""),
        ]
    elif resource_kind == "fhrp_group_assignment":
        interfaces = sorted((name, value) for name, value in relationships.items() if name.startswith("interface_"))
        if attributes.get("/interface_type") and len(interfaces) != 1:
            raise ValueError("The FHRP assignment targets an interface outside the supported DCIM graph.")
        if len(interfaces) != 1:
            raise ValueError("An FHRP assignment requires exactly one supported interface.")
        parts = [resource_kind, required_relationship("group"), interfaces]
    elif resource_kind == "service":
        parents = sorted((name, value) for name, value in relationships.items() if name.startswith("parent_"))
        if attributes.get("/parent_object_type") and len(parents) != 1:
            raise ValueError("The service parent targets a model outside the supported provider graph.")
        if len(parents) != 1:
            raise ValueError("A service requires exactly one supported parent.")
        ports = required_attribute("ports")
        if not isinstance(ports, list):
            raise ValueError("Service ports must be a list.")
        parts = [resource_kind, parents, required_attribute("name"), required_attribute("protocol"), sorted(ports)]
    elif resource_kind == "location":
        parts = [
            resource_kind,
            required_relationship("site"),
            relationships.get("parent", "root"),
            required_attribute("slug"),
        ]
    elif resource_kind == "platform":
        parts = [resource_kind, relationships.get("manufacturer", "global"), required_attribute("slug")]
    elif resource_kind in {"device_type", "rack_type"}:
        parts = [resource_kind, required_relationship("manufacturer"), required_attribute("model")]
    elif resource_kind == "rack":
        parts = [
            resource_kind,
            required_relationship("site"),
            relationships.get("location", "site-root"),
            required_attribute("name"),
        ]
    elif resource_kind in {"module_type_profile", "virtual_chassis", "cable_bundle"}:
        parts = [resource_kind, "name", required_attribute("name")]
    elif resource_kind == "inventory_item_role":
        parts = [resource_kind, "slug", required_attribute("slug")]
    elif resource_kind == "module_type":
        parts = [resource_kind, required_relationship("manufacturer"), required_attribute("model")]
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
        parent = relationships.get("device_type") or relationships.get("module_type")
        if not isinstance(parent, str) or not parent:
            raise ValueError("A component template requires exactly one device_type or module_type relationship.")
        if relationships.get("device_type") and relationships.get("module_type"):
            raise ValueError("A component template cannot belong to both a device type and module type.")
        parts = [resource_kind, parent, required_attribute("name")]
    elif resource_kind == "device_bay_template":
        parts = [resource_kind, required_relationship("device_type"), required_attribute("name")]
    elif resource_kind == "inventory_item_template":
        parts = [
            resource_kind,
            required_relationship("device_type"),
            relationships.get("parent", "root"),
            required_attribute("name"),
        ]
    elif resource_kind == "device":
        name = attributes.get("/name")
        if name:
            parts = [
                resource_kind,
                required_relationship("site"),
                relationships.get("tenant", "no-tenant"),
                "name",
                str(name).casefold(),
            ]
        elif attributes.get("/asset_tag"):
            parts = [resource_kind, "asset_tag", required_attribute("asset_tag")]
        elif relationships.get("virtual_chassis") and attributes.get("/vc_position") is not None:
            parts = [resource_kind, relationships["virtual_chassis"], "position", attributes["/vc_position"]]
        elif relationships.get("rack") and attributes.get("/position") is not None and attributes.get("/face"):
            parts = [
                resource_kind,
                relationships["rack"],
                "position",
                attributes["/position"],
                attributes["/face"],
            ]
        else:
            raise ValueError(
                "A device requires a name, asset tag, virtual chassis position, or rack position for portable identity."
            )
    elif resource_kind == "virtual_device_context":
        parts = [resource_kind, required_relationship("device"), required_attribute("name")]
    elif resource_kind == "module":
        parts = [resource_kind, required_relationship("module_bay")]
    elif resource_kind == "module_bay":
        parts = [
            resource_kind,
            required_relationship("device"),
            relationships.get("module", "device-root"),
            required_attribute("name"),
        ]
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
        parts = [resource_kind, required_relationship("device"), required_attribute("name")]
    elif resource_kind == "inventory_item":
        parts = [
            resource_kind,
            required_relationship("device"),
            relationships.get("parent", "root"),
            required_attribute("name"),
        ]
    elif resource_kind == "mac_address":
        if attributes.get("/assigned_object_type") and not any(
            name.startswith("assigned_") for name in relationships
        ):
            raise ValueError(
                "The MAC address assignment targets a model outside the supported DCIM dependency graph."
            )
        assigned = sorted((name, value) for name, value in relationships.items() if name.startswith("assigned_"))
        parts = [resource_kind, required_attribute("mac_address"), assigned or "unassigned"]
    elif resource_kind == "rack_reservation":
        units = required_attribute("units")
        if not isinstance(units, list):
            raise ValueError("Rack reservation units must be a list.")
        parts = [resource_kind, required_relationship("rack"), sorted(units), required_relationship("user")]
    elif resource_kind == "power_panel":
        parts = [resource_kind, required_relationship("site"), required_attribute("name")]
    elif resource_kind == "power_feed":
        parts = [resource_kind, required_relationship("power_panel"), required_attribute("name")]
    elif resource_kind in {"provider", "circuit_type", "virtual_circuit_type", "circuit_group"}:
        parts = [resource_kind, "slug", required_attribute("slug")]
    elif resource_kind == "provider_account":
        parts = [resource_kind, required_relationship("provider"), required_attribute("account")]
    elif resource_kind == "provider_network":
        parts = [resource_kind, required_relationship("provider"), required_attribute("name")]
    elif resource_kind == "circuit":
        parts = [resource_kind, required_relationship("provider"), required_attribute("cid")]
    elif resource_kind == "circuit_termination":
        parts = [resource_kind, required_relationship("circuit"), required_attribute("term_side")]
    elif resource_kind == "virtual_circuit":
        parts = [resource_kind, required_relationship("provider_network"), required_attribute("cid")]
    elif resource_kind == "virtual_circuit_termination":
        parts = [resource_kind, required_relationship("interface")]
    elif resource_kind == "circuit_group_assignment":
        members = sorted((name, value) for name, value in relationships.items() if name.startswith("member_"))
        if len(members) != 1:
            raise ValueError("A circuit group assignment requires exactly one supported member.")
        parts = [resource_kind, required_relationship("group"), members]
    elif resource_kind == "cable":
        unsupported_terminations = attributes.get("/unsupported_termination_types")
        if unsupported_terminations:
            raise ValueError(
                "The cable includes terminations outside the supported DCIM dependency graph: "
                + ", ".join(str(value) for value in unsupported_terminations)
                + "."
            )
        terminations = sorted(
            (name, sorted(value) if isinstance(value, list) else [value])
            for name, value in relationships.items()
            if name.startswith(("termination_a_", "termination_b_"))
        )
        if not terminations:
            raise ValueError("A cable requires at least one supported DCIM termination for portable identity.")
        parts = [resource_kind, terminations]
    else:
        raise ValueError(f"Resource kind {resource_kind!r} is not supported by the comparison target.")
    return json.dumps(normalize_value(parts), ensure_ascii=False, separators=(",", ":"))


def compare_canonical_records(
    source_records: list[CanonicalRecord],
    target_records: list[CanonicalRecord],
) -> list[ComparisonResult]:
    source_by_uid = {record.uid: record for record in source_records}
    target_by_uid = {record.uid: record for record in target_records}
    source_adapter, target_adapter = build_adapter_pair(source_records, target_records)

    diff = ComparisonOnlyDiffSyncEngine().compare(source_adapter, target_adapter)
    diff_elements = {
        f"{element.type}:{element.keys['identity_key']}": element
        for element in diff.get_children()
        if str(element.action) in {"create", "update"}
    }
    results: list[ComparisonResult] = []
    for uid, source in source_by_uid.items():
        target = target_by_uid.get(uid)
        if target is None:
            element = diff_elements[uid]
            results.append(
                ComparisonResult(
                    action=ComparisonAction.CREATE,
                    source=source,
                    target=None,
                    changes=_diffsync_field_changes(element, getattr(source_adapter, element.type)),
                    match_basis="no_target_match",
                    reason="No local NetBox object has the same exact natural identity.",
                )
            )
        elif uid in diff_elements:
            element = diff_elements[uid]
            results.append(
                ComparisonResult(
                    action=ComparisonAction.UPDATE,
                    source=source,
                    target=target,
                    changes=_diffsync_field_changes(element, getattr(source_adapter, element.type)),
                    reason="The exact target object differs on one or more observed fields.",
                )
            )
        else:
            results.append(
                ComparisonResult(
                    action=ComparisonAction.NO_CHANGE,
                    source=source,
                    target=target,
                    reason="The exact target object matches every observed field.",
                )
            )
    return results


def _diffsync_field_changes(element: Any, model_class: Any) -> tuple[dict[str, Any], ...]:
    """Translate a typed DiffSync element into the durable comparison field format."""

    differences = element.get_attrs_diffs()
    old_values = differences.get("-", {})
    new_values = differences.get("+", {})
    changed_fields = sorted(
        set(old_values) | set(new_values),
        key=lambda name: (0 if model_class._canonical_fields[name][0] == "attributes" else 1, name),
    )
    changes: list[dict[str, Any]] = []
    for field_name in changed_fields:
        category, canonical_name = model_class._canonical_fields[field_name]
        target_present, target_value = old_values.get(field_name, (False, None))
        source_present, source_value = new_values.get(field_name, (False, None))
        if not source_present and not target_present:
            continue
        changes.append(
            {
                "field": f"{category}:{canonical_name}",
                "source_present": source_present,
                "source_value": source_value,
                "target_present": target_present,
                "target_value": target_value,
            }
        )
    return tuple(changes)


def snapshot_digest(records: list[CanonicalRecord]) -> str:
    import hashlib

    payload = [
        {
            "uid": record.uid,
            "attributes": record.attributes,
            "relationships": record.relationships,
            "target_object_type": record.target_object_type,
            "target_object_id": record.target_object_id,
        }
        for record in sorted(records, key=lambda item: item.uid)
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
