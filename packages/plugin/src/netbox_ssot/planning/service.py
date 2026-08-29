from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from django.db import connection, transaction

from ..models import CollectionRun, ComparisonItem, ComparisonRun, SynchronizationDirection
from .comparison import (
    ENGINE_VERSION,
    SUPPORTED_RESOURCE_KINDS,
    CanonicalRecord,
    ComparisonAction,
    ComparisonResult,
    compare_canonical_records,
    natural_identity,
    normalize_relationship_cardinality,
    normalize_value,
    snapshot_digest,
)
from .netbox_target import load_netbox_target_records
from .resource_registry import is_identity_relationship


class ComparisonRejectedError(ValueError):
    """The selected evidence cannot safely produce a comparison preview."""


@dataclass(frozen=True, slots=True)
class ComparisonOutcome:
    comparison: ComparisonRun
    created: bool


@dataclass(frozen=True, slots=True)
class ItemDraft:
    action: ComparisonAction
    resource_kind: str
    identity_key: str
    display_name: str
    source_external_id: str
    target_object_type: str = ""
    target_object_id: str = ""
    match_basis: str = ""
    reason: str = ""
    source_data: dict[str, Any] | None = None
    target_data: dict[str, Any] | None = None
    changes: tuple[dict[str, Any], ...] = ()


def create_comparison(
    collection_run: CollectionRun,
    *,
    direction: str = SynchronizationDirection.SOURCE_TO_TARGET,
) -> ComparisonOutcome:
    if collection_run.state != "complete":
        raise ComparisonRejectedError("Only complete collection runs can be compared.")
    if not collection_run.completeness_token:
        raise ComparisonRejectedError("The collection run has no completeness token.")
    if direction != SynchronizationDirection.SOURCE_TO_TARGET:
        raise ComparisonRejectedError(
            "This provider currently advertises read capability only; target-to-source synchronization is unavailable."
        )

    with transaction.atomic():
        if connection.vendor == "postgresql" and len(connection.atomic_blocks) == 1:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")

        source_records, rejected = _load_source_records(collection_run)
        target_records = load_netbox_target_records(datasets=collection_run.datasets)
        target_digest = snapshot_digest(target_records)
        existing = ComparisonRun.objects.filter(
            collection_run=collection_run,
            target_snapshot_digest=target_digest,
            engine_version=ENGINE_VERSION,
            direction=direction,
        ).first()
        if existing is not None:
            return ComparisonOutcome(existing, False)

        drafts = _build_drafts(source_records, target_records, rejected)
        counts = Counter(draft.action.value for draft in drafts)
        comparison = ComparisonRun.objects.create(
            collection_run=collection_run,
            source_payload_digest=collection_run.payload_digest,
            target_snapshot_digest=target_digest,
            engine_version=ENGINE_VERSION,
            direction=direction,
            create_count=counts[ComparisonAction.CREATE.value],
            update_count=counts[ComparisonAction.UPDATE.value],
            no_change_count=counts[ComparisonAction.NO_CHANGE.value],
            conflict_count=counts[ComparisonAction.CONFLICT.value],
            skipped_count=counts[ComparisonAction.SKIPPED.value],
        )
        ComparisonItem.objects.bulk_create(
            [
                ComparisonItem(
                    comparison=comparison,
                    sequence=sequence,
                    action=draft.action.value,
                    resource_kind=draft.resource_kind,
                    identity_key=draft.identity_key,
                    display_name=draft.display_name,
                    source_external_id=draft.source_external_id,
                    target_object_type=draft.target_object_type,
                    target_object_id=draft.target_object_id,
                    match_basis=draft.match_basis,
                    reason=draft.reason,
                    source_data=draft.source_data or {},
                    target_data=draft.target_data or {},
                    changes=list(draft.changes),
                )
                for sequence, draft in enumerate(drafts)
            ],
            batch_size=1_000,
        )
        return ComparisonOutcome(comparison, True)


def _load_source_records(collection_run: CollectionRun) -> tuple[list[CanonicalRecord], list[ItemDraft]]:
    observations = list(collection_run.stored_observations.all())
    by_external_id = {observation.external_id: observation for observation in observations}
    resolved: dict[str, CanonicalRecord] = {}
    identities: dict[str, str] = {}
    failures: dict[str, str] = {}
    resolving: set[str] = set()

    def resolve_identity(external_id: str) -> str:
        if external_id in identities:
            return identities[external_id]
        if external_id in failures:
            raise ValueError(failures[external_id])
        observation = by_external_id.get(external_id)
        if observation is None:
            raise ValueError(f"Relationship target {external_id!r} is absent from the collection run.")
        if external_id in resolving:
            raise ValueError(f"Relationship cycle encountered at {external_id!r}.")
        resolving.add(external_id)
        try:
            attributes = {
                str(item["path"]): _normalize_attribute(str(item["path"]), item.get("value"))
                for item in observation.attributes
            }
            relationship_values: dict[str, list[str]] = defaultdict(list)
            for relationship in observation.relationships:
                relationship_name = str(relationship["kind"])
                if is_identity_relationship(observation.resource_kind, relationship_name):
                    relationship_values[relationship_name].append(
                        resolve_identity(str(relationship["target_external_id"]))
                    )
            relationships = normalize_relationship_cardinality(observation.resource_kind, relationship_values)
            identity_key = natural_identity(observation.resource_kind, attributes, relationships)
            identities[external_id] = identity_key
            return identity_key
        except (KeyError, TypeError, ValueError) as exc:
            failures[external_id] = str(exc)
            raise
        finally:
            resolving.remove(external_id)

    def resolve(external_id: str) -> CanonicalRecord:
        if external_id in resolved:
            return resolved[external_id]
        observation = by_external_id.get(external_id)
        if observation is None:
            raise ValueError(f"Relationship target {external_id!r} is absent from the collection run.")
        attributes = {
            str(item["path"]): _normalize_attribute(str(item["path"]), item.get("value"))
            for item in observation.attributes
        }
        relationship_values: dict[str, list[str]] = defaultdict(list)
        for relationship in observation.relationships:
            target_external_id = str(relationship["target_external_id"])
            relationship_values[str(relationship["kind"])].append(resolve_identity(target_external_id))
        relationships = normalize_relationship_cardinality(observation.resource_kind, relationship_values)
        identity_key = resolve_identity(external_id)
        try:
            record = CanonicalRecord(
                resource_kind=observation.resource_kind,
                identity_key=identity_key,
                display_name=_display_name(attributes, observation.external_id),
                external_id=observation.external_id,
                attributes=attributes,
                relationships=relationships,
            )
            resolved[external_id] = record
            return record
        except (KeyError, TypeError, ValueError) as exc:
            failures[external_id] = str(exc)
            raise

    rejected: list[ItemDraft] = []
    for observation in observations:
        if observation.resource_kind not in SUPPORTED_RESOURCE_KINDS:
            attributes = {
                str(item.get("path", "")): _normalize_attribute(str(item.get("path", "")), item.get("value"))
                for item in observation.attributes
                if item.get("path")
            }
            rejected.append(
                ItemDraft(
                    action=ComparisonAction.SKIPPED,
                    resource_kind=observation.resource_kind,
                    identity_key=observation.external_id,
                    display_name=_display_name(attributes, observation.external_id),
                    source_external_id=observation.external_id,
                    match_basis="unsupported_resource_kind",
                    reason="This resource kind is outside the installed NetBox compatibility scope.",
                    source_data={"attributes": attributes, "relationships": observation.relationships},
                )
            )
            continue
        try:
            resolve(observation.external_id)
        except (KeyError, TypeError, ValueError) as exc:
            attributes = {
                str(item.get("path", "")): _normalize_attribute(str(item.get("path", "")), item.get("value"))
                for item in observation.attributes
                if item.get("path")
            }
            rejected.append(
                ItemDraft(
                    action=ComparisonAction.SKIPPED,
                    resource_kind=observation.resource_kind,
                    identity_key=observation.external_id,
                    display_name=_display_name(attributes, observation.external_id),
                    source_external_id=observation.external_id,
                    match_basis="unresolved_identity",
                    reason=str(exc),
                    source_data={"attributes": attributes, "relationships": observation.relationships},
                )
            )
    return [record for record in resolved.values() if record.resource_kind in SUPPORTED_RESOURCE_KINDS], rejected


def _build_drafts(
    source_records: list[CanonicalRecord],
    target_records: list[CanonicalRecord],
    rejected: list[ItemDraft],
) -> list[ItemDraft]:
    source_groups = _group_by_uid(source_records)
    target_groups = _group_by_uid(target_records)
    drafts = list(rejected)
    comparable_source: list[CanonicalRecord] = []
    comparable_target: list[CanonicalRecord] = []

    for uid, records in source_groups.items():
        if len(records) > 1:
            drafts.extend(
                (
                    _conflict(
                        record,
                        "duplicate_source_identity",
                        f"{len(records)} source observations resolve to the same natural identity.",
                    )
                )
                for record in records
            )
            continue
        record = records[0]
        targets = target_groups.get(uid, [])
        if len(targets) > 1:
            drafts.append(
                _conflict(
                    record,
                    "ambiguous_target_identity",
                    f"{len(targets)} local NetBox objects share the same natural identity.",
                )
            )
            continue
        comparable_source.append(record)
        if targets:
            comparable_target.append(targets[0])

    results = compare_canonical_records(comparable_source, comparable_target)
    drafts.extend(_result_to_draft(result) for result in results)
    return sorted(drafts, key=lambda item: (item.resource_kind, item.identity_key, item.source_external_id))


def _group_by_uid(records: list[CanonicalRecord]) -> dict[str, list[CanonicalRecord]]:
    groups: dict[str, list[CanonicalRecord]] = defaultdict(list)
    for record in records:
        groups[record.uid].append(record)
    return groups


def _conflict(record: CanonicalRecord, match_basis: str, reason: str) -> ItemDraft:
    return ItemDraft(
        action=ComparisonAction.CONFLICT,
        resource_kind=record.resource_kind,
        identity_key=record.identity_key,
        display_name=record.display_name,
        source_external_id=record.external_id,
        match_basis=match_basis,
        reason=reason,
        source_data=record.payload,
    )


def _result_to_draft(result: ComparisonResult) -> ItemDraft:
    target = result.target
    return ItemDraft(
        action=result.action,
        resource_kind=result.source.resource_kind,
        identity_key=result.source.identity_key,
        display_name=result.source.display_name,
        source_external_id=result.source.external_id,
        target_object_type=target.target_object_type if target else "",
        target_object_id=target.target_object_id if target else "",
        match_basis=result.match_basis,
        reason=result.reason,
        source_data=result.source.payload,
        target_data=target.payload if target else {},
        changes=result.changes,
    )


def _display_name(attributes: dict[str, Any], fallback: str) -> str:
    for path in (
        "/name",
        "/username",
        "/ssid",
        "/cid",
        "/account",
        "/asn",
        "/model",
        "/address",
        "/prefix",
        "/slug",
    ):
        if value := attributes.get(path):
            return str(value)
    return fallback


def _normalize_attribute(path: str, value: Any) -> Any:
    normalized = normalize_value(value)
    if path in {"/object_types", "/tags"} and isinstance(normalized, list):
        return sorted(normalized, key=str)
    return normalized
