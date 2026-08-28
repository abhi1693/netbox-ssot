from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
}
REQUIRED_RELATIONSHIPS = {"asn": frozenset({"rir"}), "location": frozenset({"site"})}
MULTI_RELATIONSHIPS = {
    "tenant_group": frozenset({"tag"}),
    "tenant": frozenset({"tag"}),
    "site_group": frozenset({"tag"}),
    "rir": frozenset({"tag"}),
    "asn": frozenset({"tag"}),
    "region": frozenset({"tag"}),
    "site": frozenset({"asn", "tag"}),
    "location": frozenset({"tag"}),
}


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
        record_dependencies = {key for key in relationship_dependencies(record) if key in records_by_key}
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
    return tuple(sorted(requirements))


def relationship_dependencies(record: ApplicationRecord) -> tuple[tuple[str, str], ...]:
    target_kinds = RELATIONSHIP_TARGETS.get(record.resource_kind)
    if target_kinds is None:
        raise ApplicationPlanError(f"Resource kind {record.resource_kind!r} cannot be applied.")
    dependencies: list[tuple[str, str]] = []
    required = REQUIRED_RELATIONSHIPS.get(record.resource_kind, frozenset())
    multi_value = MULTI_RELATIONSHIPS.get(record.resource_kind, frozenset())
    for relationship_name, target_kind in target_kinds.items():
        value = record.relationships.get(relationship_name)
        if value in (None, "", []):
            if relationship_name in required:
                raise ApplicationPlanError(
                    f"{record.resource_kind} {record.identity_key} requires relationship {relationship_name}."
                )
            continue
        if relationship_name in multi_value and not isinstance(value, list):
            raise ApplicationPlanError(
                f"Relationship {relationship_name} on {record.resource_kind} must contain an identity list."
            )
        if relationship_name not in multi_value and isinstance(value, list):
            raise ApplicationPlanError(
                f"Relationship {relationship_name} on {record.resource_kind} must contain one identity string."
            )
        values = value if isinstance(value, list) else [value]
        for identity in values:
            if not isinstance(identity, str) or not identity:
                raise ApplicationPlanError(
                    f"Relationship {relationship_name} on {record.resource_kind} must contain identity strings."
                )
            dependencies.append((target_kind, identity))
    return tuple(dependencies)


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
