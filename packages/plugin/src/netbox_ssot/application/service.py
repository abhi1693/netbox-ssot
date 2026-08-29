from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from netbox.plugins import get_plugin_config

from ..models import ApplyItem, ApplyRun, ComparisonItem, ComparisonReview, ComparisonRun, ObjectBinding
from ..planning.comparison import ENGINE_VERSION, SUPPORTED_RESOURCE_KINDS, CanonicalRecord, snapshot_digest
from ..planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records
from ..review import review_integrity_issue
from .planning import (
    ApplicationPlanError,
    ApplicationRecord,
    ReferenceRequirement,
    dependency_order,
    external_reference_requirements,
    relationship_dependencies,
)

APPLY_ACTIONS = frozenset(
    {
        ComparisonItem.Action.CREATE,
        ComparisonItem.Action.UPDATE,
        ComparisonItem.Action.NO_CHANGE,
    }
)


class ApplicationRejectedError(ValueError):
    """Raised when a comparison cannot be safely applied."""


@dataclass(frozen=True, slots=True)
class ApplicationReadiness:
    ready: bool
    reasons: tuple[str, ...]
    current_target_digest: str


@dataclass(frozen=True, slots=True)
class ApplicationOutcome:
    apply_run: ApplyRun
    created: bool


def inspect_application(comparison: ComparisonRun, applied_by: Any | None = None) -> ApplicationReadiness:
    target_records = load_netbox_target_records()
    reasons = _readiness_reasons(comparison, target_records, applied_by=applied_by)
    return ApplicationReadiness(
        ready=not reasons,
        reasons=tuple(reasons),
        current_target_digest=snapshot_digest(target_records),
    )


def apply_comparison(comparison: ComparisonRun, applied_by: Any) -> ApplicationOutcome:
    try:
        with transaction.atomic():
            _set_apply_transaction_isolation()
            _serialize_apply_operations()
            locked = (
                ComparisonRun.objects.select_for_update()
                .select_related("collection_run", "collection_run__source")
                .get(pk=comparison.pk)
            )
            existing = ApplyRun.objects.filter(comparison=locked).first()
            if existing is not None:
                return ApplicationOutcome(existing, False)

            target_records = load_netbox_target_records()
            reasons = _readiness_reasons(locked, target_records, applied_by=applied_by)
            if reasons:
                raise ApplicationRejectedError(" ".join(reasons))

            items = list(locked.items.all())
            records_by_item = _records_by_item(items)
            mutable_records = [
                records_by_item[item.pk]
                for item in items
                if item.action in {ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE}
            ]
            reference_objects, reference_problems = _resolve_external_references(mutable_records)
            if reference_problems:
                raise ApplicationRejectedError(_reference_problem_message(reference_problems))

            objects_by_item = _apply_items(
                items,
                records_by_item,
                target_records,
                reference_objects,
            )
            apply_run = ApplyRun.objects.create(
                comparison=locked,
                applied_by=applied_by,
                create_count=locked.create_count,
                update_count=locked.update_count,
                no_change_count=locked.no_change_count,
            )
            ApplyItem.objects.bulk_create(
                [
                    ApplyItem(
                        apply_run=apply_run,
                        comparison_item=item,
                        sequence=sequence,
                        action=item.action,
                        resource_kind=item.resource_kind,
                        source_external_id=item.source_external_id,
                        target_object_type=objects_by_item[item.pk]._meta.label_lower,
                        target_object_id=str(objects_by_item[item.pk].pk),
                    )
                    for sequence, item in enumerate(items)
                ],
                batch_size=1_000,
            )
            _update_bindings(apply_run, items, objects_by_item)
            return ApplicationOutcome(apply_run, True)
    except ApplicationRejectedError:
        raise
    except OperationalError as exc:
        if not _is_serialization_failure(exc):
            raise
        raise ApplicationRejectedError(
            "The local NetBox target changed concurrently; create and review a fresh comparison."
        ) from exc
    except (ApplicationPlanError, IntegrityError, ValidationError) as exc:
        raise ApplicationRejectedError(f"NetBox rejected the application transaction: {exc}") from exc


def _set_apply_transaction_isolation() -> None:
    if connection.vendor != "postgresql":
        return
    if len(connection.atomic_blocks) != 1:
        raise ApplicationRejectedError("Apply must run in its own top-level database transaction.")
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")


def _is_serialization_failure(exc: OperationalError) -> bool:
    cause = exc.__cause__
    return getattr(cause, "sqlstate", None) == "40001"


def _readiness_reasons(
    comparison: ComparisonRun,
    target_records: list[CanonicalRecord],
    *,
    applied_by: Any | None = None,
) -> list[str]:
    reasons: list[str] = []
    if ApplyRun.objects.filter(comparison=comparison).exists():
        reasons.append("This comparison has already been applied.")
    if comparison.engine_version != ENGINE_VERSION:
        reasons.append("This comparison was produced by an obsolete engine version; create a fresh comparison.")
    if comparison.collection_run.state != "complete" or not comparison.collection_run.completeness_token:
        reasons.append("Only complete collection evidence can be applied.")
    if comparison.source_payload_digest != comparison.collection_run.payload_digest:
        reasons.append("The comparison source digest no longer matches its immutable collection run.")
    if comparison.conflict_count:
        reasons.append(f"Resolve all {comparison.conflict_count} conflict items before applying.")
    if comparison.skipped_count:
        reasons.append(f"A comparison containing {comparison.skipped_count} skipped items cannot be applied.")

    review = ComparisonReview.objects.filter(comparison=comparison).select_related("reviewed_by").first()
    if comparison.create_count or comparison.update_count:
        if review is None:
            reasons.append("This comparison must be approved in a finalized review before it can be applied.")
        elif review.decision == ComparisonReview.Decision.REJECTED:
            reasons.append("This comparison was rejected and cannot be applied.")
        elif review.decision == ComparisonReview.Decision.APPROVED:
            integrity_issue = review_integrity_issue(review)
            if integrity_issue:
                reasons.append(integrity_issue)
            if (
                applied_by is not None
                and _separate_reviewer_required()
                and review.reviewed_by_id == applied_by.pk
            ):
                reasons.append("A different operator must apply this approved comparison.")
        else:
            reasons.append("This comparison has an unsupported final review state and cannot be applied.")

    items = list(comparison.items.all())
    counts = Counter(item.action for item in items)
    expected_counts = {
        ComparisonItem.Action.CREATE: comparison.create_count,
        ComparisonItem.Action.UPDATE: comparison.update_count,
        ComparisonItem.Action.NO_CHANGE: comparison.no_change_count,
        ComparisonItem.Action.CONFLICT: comparison.conflict_count,
        ComparisonItem.Action.SKIPPED: comparison.skipped_count,
    }
    if any(counts[action] != expected for action, expected in expected_counts.items()):
        reasons.append("The comparison summary does not match its immutable item set.")
    if any(item.resource_kind not in SUPPORTED_RESOURCE_KINDS for item in items if item.action in APPLY_ACTIONS):
        reasons.append("The comparison contains an actionable resource kind outside the supported scope.")

    current_digest = snapshot_digest(target_records)
    if comparison.target_snapshot_digest != current_digest:
        reasons.append("The local NetBox target changed after this comparison; create and review a fresh comparison.")

    if reasons:
        return reasons
    try:
        records_by_item = _records_by_item(items)
        records = list(records_by_item.values())
        dependency_order(records)
        reasons.extend(_relationship_problems(records, target_records))
        mutable_records = [
            records_by_item[item.pk]
            for item in items
            if item.action in {ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE}
        ]
        _, reference_problems = _resolve_external_references(mutable_records)
        if reference_problems:
            reasons.append(_reference_problem_message(reference_problems))
        reasons.extend(_content_type_problems(mutable_records))
    except ApplicationPlanError as exc:
        reasons.append(str(exc))
    return reasons


def _separate_reviewer_required() -> bool:
    return bool(get_plugin_config("netbox_ssot", "require_separate_reviewer_and_applier"))


def _records_by_item(items: list[ComparisonItem]) -> dict[int, ApplicationRecord]:
    records: dict[int, ApplicationRecord] = {}
    for item in items:
        if item.action not in APPLY_ACTIONS:
            continue
        attributes = item.source_data.get("attributes")
        relationships = item.source_data.get("relationships")
        if not isinstance(attributes, dict) or not isinstance(relationships, dict):
            raise ApplicationPlanError(f"Comparison item {item.pk} has malformed source data.")
        records[item.pk] = ApplicationRecord(
            resource_kind=item.resource_kind,
            identity_key=item.identity_key,
            attributes=attributes,
            relationships=relationships,
        )
    return records


def _relationship_problems(
    records: list[ApplicationRecord],
    target_records: list[CanonicalRecord],
) -> list[str]:
    available = {(record.resource_kind, record.identity_key) for record in records}
    available.update((record.resource_kind, record.identity_key) for record in target_records)
    missing: set[tuple[str, str]] = set()
    for record in records:
        missing.update(key for key in relationship_dependencies(record) if key not in available)
    if not missing:
        return []
    examples = ", ".join(f"{kind}:{identity}" for kind, identity in sorted(missing)[:5])
    return [f"The plan references {len(missing)} dependencies that are absent: {examples}."]


def _resolve_external_references(
    records: list[ApplicationRecord],
) -> tuple[dict[ReferenceRequirement, Any], tuple[tuple[ReferenceRequirement, int], ...]]:
    resolved: dict[ReferenceRequirement, Any] = {}
    problems: list[tuple[ReferenceRequirement, int]] = []
    for requirement in external_reference_requirements(records):
        model = apps.get_model(requirement.model_label)
        matches = list(model.objects.filter(**{requirement.lookup_field: requirement.value})[:2])
        if len(matches) != 1:
            problems.append((requirement, len(matches)))
        else:
            resolved[requirement] = matches[0]
    return resolved, tuple(problems)


def _reference_problem_message(problems: tuple[tuple[ReferenceRequirement, int], ...]) -> str:
    examples = ", ".join(
        f"{requirement.model_label}.{requirement.lookup_field}={requirement.value!r}"
        f" ({'missing' if count == 0 else 'ambiguous'})"
        for requirement, count in problems[:10]
    )
    remaining = len(problems) - 10
    suffix = f", plus {remaining} more" if remaining else ""
    return (
        f"Resolve {len(problems)} external references before applying: {examples}{suffix}. "
        "References outside the installed compatibility bundle are never created implicitly."
    )


def _content_type_problems(records: list[ApplicationRecord]) -> list[str]:
    content_type_model = apps.get_model("contenttypes.contenttype")
    missing: set[str] = set()
    for record in records:
        if record.resource_kind != "tag":
            continue
        values = record.attributes.get("/object_types", [])
        if not isinstance(values, list):
            raise ApplicationPlanError("Tag attribute /object_types must be a list.")
        for value in values:
            if not isinstance(value, str) or value.count(".") != 1:
                raise ApplicationPlanError("Tag object types must use the app_label.model format.")
            app_label, model = value.split(".", 1)
            if not content_type_model.objects.filter(app_label=app_label, model=model).exists():
                missing.add(value)
    if not missing:
        return []
    return [f"The target does not provide {len(missing)} Tag object types: {', '.join(sorted(missing)[:10])}."]


def _apply_items(
    items: list[ComparisonItem],
    records_by_item: dict[int, ApplicationRecord],
    target_records: list[CanonicalRecord],
    reference_objects: dict[ReferenceRequirement, Any],
) -> dict[int, Any]:
    target_by_key = {(record.resource_kind, record.identity_key): record for record in target_records}
    if len(target_by_key) != len(target_records):
        raise ApplicationPlanError("The target snapshot contains duplicate natural identities.")

    mutable_items_by_key = {
        records_by_item[item.pk].key: item
        for item in items
        if item.action in {ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE}
    }
    ordered_records = dependency_order([records_by_item[item.pk] for item in items if item.pk in records_by_item])
    object_cache: dict[tuple[str, str], Any] = {}
    objects_by_item: dict[int, Any] = {}

    for record in ordered_records:
        item = mutable_items_by_key.get(record.key)
        if item is None:
            continue
        if item.action == ComparisonItem.Action.CREATE:
            if record.key in target_by_key:
                raise ApplicationPlanError(f"Target identity {record.identity_key} appeared after comparison.")
            obj = MODEL_BY_KIND[record.resource_kind]()
        else:
            target_record = target_by_key.get(record.key)
            if target_record is None:
                raise ApplicationPlanError(f"Update target {record.identity_key} disappeared after comparison.")
            if (
                item.target_object_type != target_record.target_object_type
                or item.target_object_id != target_record.target_object_id
            ):
                raise ApplicationPlanError(
                    f"Update target {record.identity_key} no longer matches the reviewed object."
                )
            obj = _load_target_object(target_record)
        _write_object(obj, record, target_by_key, object_cache, reference_objects)
        object_cache[record.key] = obj
        objects_by_item[item.pk] = obj

    for item in items:
        if item.action != ComparisonItem.Action.NO_CHANGE:
            continue
        record = records_by_item[item.pk]
        target_record = target_by_key.get(record.key)
        if target_record is None:
            raise ApplicationPlanError(f"Reviewed target {record.identity_key} disappeared after comparison.")
        obj = object_cache.get(record.key) or _load_target_object(target_record)
        object_cache[record.key] = obj
        objects_by_item[item.pk] = obj
    return objects_by_item


def _write_object(
    obj: Any,
    record: ApplicationRecord,
    target_by_key: dict[tuple[str, str], CanonicalRecord],
    object_cache: dict[tuple[str, str], Any],
    references: dict[ReferenceRequirement, Any],
) -> None:
    attributes = record.attributes
    relationships = record.relationships
    kind = record.resource_kind

    if kind == "owner_group":
        obj.name = _required(attributes, "/name")
        obj.description = attributes.get("/description", "")
    elif kind == "owner":
        obj.name = _required(attributes, "/name")
        obj.description = attributes.get("/description", "")
        obj.group = _relationship_object("owner_group", relationships.get("group"), target_by_key, object_cache)
    elif kind == "tag":
        obj.name = _required(attributes, "/name")
        obj.slug = _required(attributes, "/slug")
        obj.color = _required(attributes, "/color")
        obj.description = attributes.get("/description", "")
        obj.weight = _required(attributes, "/weight")
        obj.owner = _relationship_object("owner", relationships.get("owner"), target_by_key, object_cache)
    elif kind in {"tenant_group", "site_group"}:
        obj.name = _required(attributes, "/name")
        obj.slug = _required(attributes, "/slug")
        obj.description = attributes.get("/description", "")
        obj.comments = attributes.get("/comments", "")
        obj.parent = _relationship_object(kind, relationships.get("parent"), target_by_key, object_cache)
        obj.owner = _relationship_object("owner", relationships.get("owner"), target_by_key, object_cache)
    elif kind == "tenant":
        obj.name = _required(attributes, "/name")
        obj.slug = _required(attributes, "/slug")
        obj.description = attributes.get("/description", "")
        obj.comments = attributes.get("/comments", "")
        obj.group = _relationship_object("tenant_group", relationships.get("group"), target_by_key, object_cache)
        obj.owner = _relationship_object("owner", relationships.get("owner"), target_by_key, object_cache)
    elif kind == "rir":
        obj.name = _required(attributes, "/name")
        obj.slug = _required(attributes, "/slug")
        obj.is_private = _required(attributes, "/is_private")
        obj.description = attributes.get("/description", "")
        obj.comments = attributes.get("/comments", "")
        obj.owner = _relationship_object("owner", relationships.get("owner"), target_by_key, object_cache)
    elif kind == "asn":
        obj.asn = _required(attributes, "/asn")
        obj.rir = _relationship_object(
            "rir",
            relationships.get("rir"),
            target_by_key,
            object_cache,
            required=True,
        )
        obj.role = _scalar_reference(references, "ipam.role", "slug", attributes.get("/role"))
        obj.tenant = _relationship_object("tenant", relationships.get("tenant"), target_by_key, object_cache)
        obj.owner = _relationship_object("owner", relationships.get("owner"), target_by_key, object_cache)
        obj.description = attributes.get("/description", "")
        obj.comments = attributes.get("/comments", "")
    elif kind == "region":
        obj.name = _required(attributes, "/name")
        obj.slug = _required(attributes, "/slug")
        obj.description = attributes.get("/description", "")
        obj.comments = attributes.get("/comments", "")
        obj.parent = _relationship_object(
            "region",
            relationships.get("parent"),
            target_by_key,
            object_cache,
        )
        obj.owner = _relationship_object("owner", relationships.get("owner"), target_by_key, object_cache)
    elif kind == "site":
        obj.name = _required(attributes, "/name")
        obj.slug = _required(attributes, "/slug")
        obj.description = attributes.get("/description", "")
        obj.comments = attributes.get("/comments", "")
        obj.status = _required(attributes, "/status")
        obj.region = _relationship_object(
            "region",
            relationships.get("region"),
            target_by_key,
            object_cache,
        )
        obj.group = _relationship_object("site_group", relationships.get("group"), target_by_key, object_cache)
        obj.tenant = _relationship_object("tenant", relationships.get("tenant"), target_by_key, object_cache)
        obj.owner = _relationship_object("owner", relationships.get("owner"), target_by_key, object_cache)
        obj.facility = attributes.get("/facility", "")
        obj.time_zone = attributes.get("/time_zone")
        obj.physical_address = attributes.get("/physical_address", "")
        obj.shipping_address = attributes.get("/shipping_address", "")
        obj.latitude = attributes.get("/latitude")
        obj.longitude = attributes.get("/longitude")
    elif kind == "location":
        obj.name = _required(attributes, "/name")
        obj.slug = _required(attributes, "/slug")
        obj.description = attributes.get("/description", "")
        obj.comments = attributes.get("/comments", "")
        obj.status = _required(attributes, "/status")
        obj.site = _relationship_object(
            "site",
            relationships.get("site"),
            target_by_key,
            object_cache,
            required=True,
        )
        obj.parent = _relationship_object(
            "location",
            relationships.get("parent"),
            target_by_key,
            object_cache,
        )
        obj.tenant = _relationship_object("tenant", relationships.get("tenant"), target_by_key, object_cache)
        obj.owner = _relationship_object("owner", relationships.get("owner"), target_by_key, object_cache)
        obj.facility = attributes.get("/facility", "")
    else:
        raise ApplicationPlanError(f"Resource kind {record.resource_kind!r} cannot be written.")

    obj.full_clean()
    obj.save()
    if kind == "tag":
        obj.object_types.set(_content_types(attributes.get("/object_types", [])))
    if kind in {"tenant_group", "tenant", "site_group", "rir", "asn", "region", "site", "location"}:
        obj.tags.set(_relationship_objects("tag", relationships.get("tag"), target_by_key, object_cache))
    if kind == "site":
        obj.asns.set(_relationship_objects("asn", relationships.get("asn"), target_by_key, object_cache))


def _relationship_object(
    resource_kind: str,
    identity_key: Any,
    target_by_key: dict[tuple[str, str], CanonicalRecord],
    object_cache: dict[tuple[str, str], Any],
    *,
    required: bool = False,
) -> Any | None:
    if identity_key in (None, ""):
        if required:
            raise ApplicationPlanError(f"A {resource_kind} relationship is required.")
        return None
    if not isinstance(identity_key, str):
        raise ApplicationPlanError(f"A scalar {resource_kind} relationship must contain one identity string.")
    key = resource_kind, identity_key
    if key in object_cache:
        return object_cache[key]
    target_record = target_by_key.get(key)
    if target_record is None:
        raise ApplicationPlanError(f"Relationship target {resource_kind}:{identity_key} is unavailable.")
    obj = _load_target_object(target_record)
    object_cache[key] = obj
    return obj


def _relationship_objects(
    resource_kind: str,
    identity_keys: Any,
    target_by_key: dict[tuple[str, str], CanonicalRecord],
    object_cache: dict[tuple[str, str], Any],
) -> list[Any]:
    if identity_keys in (None, "", []):
        return []
    if not isinstance(identity_keys, list):
        raise ApplicationPlanError(f"A multi-value {resource_kind} relationship must contain an identity list.")
    return [
        _relationship_object(resource_kind, identity_key, target_by_key, object_cache, required=True)
        for identity_key in identity_keys
    ]


def _content_types(values: Any) -> list[Any]:
    if values in (None, "", []):
        return []
    if not isinstance(values, list):
        raise ApplicationPlanError("Tag attribute /object_types must be a list.")
    content_type_model = apps.get_model("contenttypes.contenttype")
    content_types: list[Any] = []
    for value in values:
        if not isinstance(value, str) or value.count(".") != 1:
            raise ApplicationPlanError("Tag object types must use the app_label.model format.")
        app_label, model = value.split(".", 1)
        content_types.append(content_type_model.objects.get(app_label=app_label, model=model))
    return content_types


def _load_target_object(record: CanonicalRecord) -> Any:
    model = apps.get_model(record.target_object_type)
    return model.objects.select_for_update().get(pk=record.target_object_id)


def _required(attributes: dict[str, Any], path: str) -> Any:
    value = attributes.get(path)
    if value in (None, ""):
        raise ApplicationPlanError(f"Required source attribute {path} is missing.")
    return value


def _scalar_reference(
    references: dict[ReferenceRequirement, Any],
    model_label: str,
    lookup_field: str,
    value: Any,
) -> Any | None:
    if value in (None, ""):
        return None
    return references[ReferenceRequirement(model_label, lookup_field, value)]


def _update_bindings(apply_run: ApplyRun, items: list[ComparisonItem], objects_by_item: dict[int, Any]) -> None:
    observations = {
        (observation.resource_kind, observation.external_id): observation
        for observation in apply_run.comparison.collection_run.stored_observations.all()
    }
    for item in items:
        observation = observations.get((item.resource_kind, item.source_external_id))
        if observation is None:
            raise ApplicationPlanError(f"Source observation {item.source_external_id!r} is unavailable.")
        obj = objects_by_item[item.pk]
        ObjectBinding.objects.update_or_create(
            source=apply_run.comparison.collection_run.source,
            resource_kind=item.resource_kind,
            source_external_id=item.source_external_id,
            defaults={
                "identity_key": item.identity_key,
                "target_object_type": obj._meta.label_lower,
                "target_object_id": str(obj.pk),
                "source_fingerprint": observation.fingerprint,
                "last_applied_run": apply_run,
            },
        )


def _serialize_apply_operations() -> None:
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [1_977_042_836])
