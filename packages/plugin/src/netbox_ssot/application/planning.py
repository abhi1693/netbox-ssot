from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..planning.comparison import MULTI_RELATIONSHIPS
from ..planning.dcim import (
    RELATIONSHIP_FIELDS,
    TAGGED_KINDS,
    is_multi_relationship,
    relationship_target,
)
from ..planning.dcim import (
    REQUIRED_RELATIONSHIPS as DCIM_REQUIRED_RELATIONSHIPS,
)

RELATIONSHIP_TARGETS: dict[str, dict[str, str]] = {
    "tag": {"owner": "owner"},
    "owner_group": {},
    "owner": {"group": "owner_group"},
    "tenant_group": {"parent": "tenant_group", "owner": "owner", "tag": "tag"},
    "tenant": {"group": "tenant_group", "owner": "owner", "tag": "tag"},
    "site_group": {"parent": "site_group", "owner": "owner", "tag": "tag"},
    "rir": {"owner": "owner", "tag": "tag"},
    "asn": {"rir": "rir", "tenant": "tenant", "owner": "owner", "tag": "tag"},
    "region": {"parent": "region", "owner": "owner", "tag": "tag"},
    "site": {
        "region": "region",
        "group": "site_group",
        "tenant": "tenant",
        "owner": "owner",
        "asn": "asn",
        "tag": "tag",
    },
    "location": {"site": "site", "parent": "location", "tenant": "tenant", "owner": "owner", "tag": "tag"},
    "manufacturer": {"owner": "owner", "tag": "tag"},
    "device_role": {"parent": "device_role", "owner": "owner", "tag": "tag"},
    "platform": {"parent": "platform", "manufacturer": "manufacturer", "owner": "owner", "tag": "tag"},
    "device_type": {
        "manufacturer": "manufacturer",
        "default_platform": "platform",
        "owner": "owner",
        "tag": "tag",
    },
    "rack_group": {"owner": "owner", "tag": "tag"},
    "rack_role": {"owner": "owner", "tag": "tag"},
    "rack_type": {"manufacturer": "manufacturer", "owner": "owner", "tag": "tag"},
    "rack": {
        "site": "site",
        "location": "location",
        "group": "rack_group",
        "tenant": "tenant",
        "role": "rack_role",
        "rack_type": "rack_type",
        "owner": "owner",
        "tag": "tag",
    },
}
for _kind, _fields in RELATIONSHIP_FIELDS.items():
    RELATIONSHIP_TARGETS[_kind] = {name: target_kind for name, (target_kind, _) in _fields.items()}
for _kind in TAGGED_KINDS:
    RELATIONSHIP_TARGETS.setdefault(_kind, {})["tag"] = "tag"
RELATIONSHIP_TARGETS["interface"]["vdc"] = "virtual_device_context"

REQUIRED_RELATIONSHIPS = {
    "asn": frozenset({"rir"}),
    "location": frozenset({"site"}),
    "device_type": frozenset({"manufacturer"}),
    "rack_type": frozenset({"manufacturer"}),
    "rack": frozenset({"site"}),
}
REQUIRED_RELATIONSHIPS.update(DCIM_REQUIRED_RELATIONSHIPS)


class ApplicationPlanError(ValueError):
    """Raised when comparison items cannot form an unambiguous apply plan."""


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    resource_kind: str
    identity_key: str
    attributes: dict[str, Any]
    relationships: dict[str, Any]

    @property
    def key(self) -> tuple[str, str]:
        return self.resource_kind, self.identity_key


@dataclass(frozen=True, order=True, slots=True)
class ReferenceRequirement:
    model_label: str
    lookup_field: str
    value: str | int


def dependency_order(records: list[ApplicationRecord]) -> list[ApplicationRecord]:
    records_by_key = {record.key: record for record in records}
    if len(records_by_key) != len(records):
        raise ApplicationPlanError("The application plan contains duplicate natural identities.")

    dependencies: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for record in records:
        record_dependencies = {
            key for key in relationship_dependencies(record, include_deferred=False) if key in records_by_key
        }
        dependencies[record.key] = record_dependencies

    ordered: list[ApplicationRecord] = []
    while dependencies:
        ready = sorted(key for key, required in dependencies.items() if not required)
        if not ready:
            unresolved = ", ".join(f"{kind}:{identity}" for kind, identity in sorted(dependencies)[:5])
            raise ApplicationPlanError(f"The application dependency graph contains a cycle near {unresolved}.")
        for key in ready:
            ordered.append(records_by_key[key])
            dependencies.pop(key)
        for required in dependencies.values():
            required.difference_update(ready)
    return ordered


def external_reference_requirements(records: list[ApplicationRecord]) -> tuple[ReferenceRequirement, ...]:
    requirements: set[ReferenceRequirement] = set()
    for record in records:
        attributes = record.attributes
        if record.resource_kind not in RELATIONSHIP_TARGETS:
            raise ApplicationPlanError(f"Resource kind {record.resource_kind!r} cannot be applied.")
        if record.resource_kind == "asn":
            _add_scalar(requirements, attributes, "/role", "ipam.role", "slug")
        if record.resource_kind in {"device_role", "platform"}:
            _add_scalar(requirements, attributes, "/config_template", "extras.configtemplate", "name")
        if record.resource_kind == "device":
            _add_scalar(requirements, attributes, "/config_template", "extras.configtemplate", "name")
        if record.resource_kind == "rack_reservation":
            _add_scalar(requirements, attributes, "/user", "users.user", "username")
    return tuple(sorted(requirements))


def relationship_dependencies(
    record: ApplicationRecord,
    *,
    include_deferred: bool = True,
) -> tuple[tuple[str, str], ...]:
    configured_targets = RELATIONSHIP_TARGETS.get(record.resource_kind)
    if configured_targets is None:
        raise ApplicationPlanError(f"Resource kind {record.resource_kind!r} cannot be applied.")
    dependencies: list[tuple[str, str]] = []
    required = REQUIRED_RELATIONSHIPS.get(record.resource_kind, frozenset())
    for relationship_name in required:
        if record.relationships.get(relationship_name) in (None, "", []):
            raise ApplicationPlanError(
                f"{record.resource_kind} {record.identity_key} requires relationship {relationship_name}."
            )
    for relationship_name, value in record.relationships.items():
        target_kind = configured_targets.get(relationship_name) or relationship_target(
            record.resource_kind, relationship_name
        )
        if target_kind is None:
            raise ApplicationPlanError(
                f"Relationship {relationship_name} on {record.resource_kind} is not supported by this provider."
            )
        if value in (None, "", []):
            continue
        multi_value = is_multi_relationship(record.resource_kind, relationship_name) or relationship_name in (
            MULTI_RELATIONSHIPS.get(record.resource_kind, frozenset())
        )
        if multi_value and not isinstance(value, list):
            raise ApplicationPlanError(
                f"Relationship {relationship_name} on {record.resource_kind} must contain an identity list."
            )
        if not multi_value and isinstance(value, list):
            raise ApplicationPlanError(
                f"Relationship {relationship_name} on {record.resource_kind} must contain one identity string."
            )
        values = value if isinstance(value, list) else [value]
        for identity in values:
            if not isinstance(identity, str) or not identity:
                raise ApplicationPlanError(
                    f"Relationship {relationship_name} on {record.resource_kind} must contain identity strings."
                )
            if include_deferred or not _deferred_relationship(record.resource_kind, relationship_name):
                dependencies.append((target_kind, identity))
    return tuple(dependencies)


def _deferred_relationship(resource_kind: str, relationship_name: str) -> bool:
    return (
        (resource_kind == "virtual_chassis" and relationship_name == "master")
        or (resource_kind == "interface" and relationship_name == "primary_mac_address")
        or relationship_name.startswith("mapping_")
    )


def _add_scalar(
    requirements: set[ReferenceRequirement],
    attributes: dict[str, Any],
    path: str,
    model_label: str,
    lookup_field: str,
) -> None:
    value = attributes.get(path)
    if value not in (None, ""):
        requirements.add(ReferenceRequirement(model_label, lookup_field, _reference_value(value)))


def _reference_value(value: Any) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ApplicationPlanError("External reference values must be strings or integers.")
    return value
