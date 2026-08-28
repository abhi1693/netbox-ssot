from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from diffsync import Adapter, DiffSyncModel

from .diffsync_engine import ComparisonOnlyDiffSyncEngine

ENGINE_VERSION = "3.0"
SUPPORTED_RESOURCE_KINDS = frozenset(
    {
        "tag",
        "owner_group",
        "owner",
        "tenant_group",
        "tenant",
        "site_group",
        "rir",
        "asn",
        "region",
        "site",
        "location",
    }
)


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


class CanonicalDiffSyncModel(DiffSyncModel):
    _modelname: ClassVar[str] = "resource"
    _identifiers: ClassVar[tuple[str, ...]] = ("uid",)
    _attributes: ClassVar[tuple[str, ...]] = ("attributes", "relationships")

    uid: str
    attributes: dict[str, Any]
    relationships: dict[str, Any]


class CanonicalAdapter(Adapter):
    resource: ClassVar[type[CanonicalDiffSyncModel]] = CanonicalDiffSyncModel
    top_level: ClassVar[list[str]] = ["resource"]


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple, set)):
        return [normalize_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def natural_identity(
    resource_kind: str,
    attributes: dict[str, Any],
    relationships: dict[str, str],
) -> str:
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
    if resource_kind in {"region", "site_group", "tenant_group"}:
        parts = [resource_kind, relationships.get("parent", "root"), required_attribute("slug")]
    elif resource_kind == "tenant":
        parts = [resource_kind, relationships.get("group", "root"), required_attribute("slug")]
    elif resource_kind in {"tag", "rir", "site"}:
        parts = [resource_kind, "slug", required_attribute("slug")]
    elif resource_kind in {"owner_group", "owner"}:
        parts = [resource_kind, "name", required_attribute("name")]
    elif resource_kind == "asn":
        parts = [resource_kind, "asn", required_attribute("asn")]
    elif resource_kind == "location":
        parts = [
            resource_kind,
            required_relationship("site"),
            relationships.get("parent", "root"),
            required_attribute("slug"),
        ]
    else:
        raise ValueError(f"Resource kind {resource_kind!r} is not supported by the comparison target.")
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def compare_canonical_records(
    source_records: list[CanonicalRecord],
    target_records: list[CanonicalRecord],
) -> list[ComparisonResult]:
    source_by_uid = {record.uid: record for record in source_records}
    target_by_uid = {record.uid: record for record in target_records}
    source_adapter = CanonicalAdapter()
    target_adapter = CanonicalAdapter()
    for record in source_records:
        source_adapter.add(
            CanonicalDiffSyncModel(
                uid=record.uid,
                attributes=record.attributes,
                relationships=record.relationships,
            )
        )
    for record in target_records:
        target_adapter.add(
            CanonicalDiffSyncModel(
                uid=record.uid,
                attributes=record.attributes,
                relationships=record.relationships,
            )
        )

    diff = ComparisonOnlyDiffSyncEngine().compare(source_adapter, target_adapter)
    changed_uids = {str(element.keys["uid"]): str(element.action) for element in diff.get_children()}
    results: list[ComparisonResult] = []
    for uid, source in source_by_uid.items():
        target = target_by_uid.get(uid)
        if target is None:
            results.append(
                ComparisonResult(
                    action=ComparisonAction.CREATE,
                    source=source,
                    target=None,
                    changes=field_changes(source, None),
                    match_basis="no_target_match",
                    reason="No local NetBox object has the same exact natural identity.",
                )
            )
        elif changed_uids.get(uid) == "update":
            results.append(
                ComparisonResult(
                    action=ComparisonAction.UPDATE,
                    source=source,
                    target=target,
                    changes=field_changes(source, target),
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


def field_changes(source: CanonicalRecord, target: CanonicalRecord | None) -> tuple[dict[str, Any], ...]:
    changes: list[dict[str, Any]] = []
    target_payload = target.payload if target is not None else {"attributes": {}, "relationships": {}}
    for category, source_fields in source.payload.items():
        target_fields = target_payload[category]
        for name in sorted(set(source_fields) | set(target_fields)):
            source_present = name in source_fields
            target_present = name in target_fields
            source_value = source_fields.get(name)
            target_value = target_fields.get(name)
            if source_present == target_present and source_value == target_value:
                continue
            changes.append(
                {
                    "field": f"{category}:{name}",
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
