from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.backends.postgresql.psycopg_any import NumericRange
from netbox.plugins import get_plugin_config
from netbox.registry import registry

from ..comparison_presentation import format_relationship_value
from ..models import (
    ApplyItem,
    ApplyRun,
    ComparisonItem,
    ComparisonReview,
    ComparisonRun,
    ObjectBinding,
    SynchronizationDirection,
)
from ..planning.adapters import NO_DELETE_FLAGS, AdapterCapabilities, build_adapter_pair
from ..planning.comparison import (
    ENGINE_VERSION,
    SUPPORTED_RESOURCE_KINDS,
    CanonicalRecord,
    natural_identity,
    snapshot_digest,
)
from ..planning.core import PORTABLE_DATA_SOURCE_PARAMETER_KEYS, portable_data_source_parameters
from ..planning.extras import CONFIG_CONTEXT_MULTI_RELATIONSHIPS, CONTENT_TYPE_LIST_KINDS
from ..planning.ipam import normalize_vlan_ranges
from ..planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records
from ..planning.resource_registry import (
    ATTRIBUTE_FIELDS,
    CUSTOM_FIELD_KINDS,
    RELATIONSHIP_FIELDS,
    TAGGED_KINDS,
    custom_field_relationship_name,
    parse_custom_field_relationship,
    relationship_target,
)
from ..review import review_integrity_issue
from .planning import (
    REQUIRED_RELATIONSHIPS,
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
    target_records = load_netbox_target_records(datasets=comparison.collection_run.datasets)
    reasons = _readiness_reasons(comparison, target_records, applied_by=applied_by)
    return ApplicationReadiness(
        ready=not reasons,
        reasons=tuple(reasons),
        current_target_digest=snapshot_digest(target_records),
    )


def inspect_application_summary(comparison: ComparisonRun, applied_by: Any | None = None) -> ApplicationReadiness:
    """Build interactive-page readiness without reconstructing the full NetBox target graph."""
    reasons = _basic_readiness_reasons(comparison, applied_by=applied_by)
    target_changed = _target_changed_since(comparison)
    if target_changed:
        reasons.append("The local NetBox target changed after this comparison; create and review a fresh comparison.")
    return ApplicationReadiness(
        ready=not reasons,
        reasons=tuple(reasons),
        current_target_digest="" if target_changed else comparison.target_snapshot_digest,
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

            target_records = load_netbox_target_records(datasets=locked.collection_run.datasets)
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

            objects_by_item = _sync_items(
                items,
                records_by_item,
                target_records,
                reference_objects,
            )
            apply_run = ApplyRun.objects.create(
                comparison=locked,
                applied_by=applied_by,
                direction=locked.direction,
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
    reasons = _basic_readiness_reasons(comparison, applied_by=applied_by)
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
        reasons.extend(_data_source_problems(mutable_records))
        reasons.extend(_extras_problems(mutable_records, target_records))
    except ApplicationPlanError as exc:
        reasons.append(str(exc))
    return reasons


def _basic_readiness_reasons(comparison: ComparisonRun, *, applied_by: Any | None = None) -> list[str]:
    reasons: list[str] = []
    if ApplyRun.objects.filter(comparison=comparison).exists():
        reasons.append("This comparison has already been applied.")
    if comparison.engine_version != ENGINE_VERSION:
        reasons.append("This comparison was produced by an obsolete engine version; create a fresh comparison.")
    if comparison.direction != SynchronizationDirection.SOURCE_TO_TARGET:
        reasons.append("The selected target adapter does not currently advertise remote write capability.")
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
            if applied_by is not None and _separate_reviewer_required() and review.reviewed_by_id == applied_by.pk:
                reasons.append("A different operator must apply this approved comparison.")
        else:
            reasons.append("This comparison has an unsupported final review state and cannot be applied.")
    return reasons


def _target_changed_since(comparison: ComparisonRun) -> bool:
    resource_kinds = comparison.items.order_by().values_list("resource_kind", flat=True).distinct()
    model_types = {
        MODEL_BY_KIND[resource_kind]
        for resource_kind in resource_kinds
        if resource_kind in MODEL_BY_KIND
    }
    for model_type in model_types:
        field_names = {field.name for field in model_type._meta.fields}
        timestamp_field = next(
            (name for name in ("last_updated", "updated_at", "updated") if name in field_names),
            None,
        )
        if timestamp_field and model_type.objects.filter(
            **{f"{timestamp_field}__gt": comparison.created_at}
        ).exists():
            return True
    return False


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
        attributes = dict(attributes)
        relationships = dict(relationships)
        if item.resource_kind == "rack_reservation" and not relationships.get("user"):
            legacy_user = attributes.pop("/user", None)
            if isinstance(legacy_user, str) and legacy_user:
                relationships["user"] = natural_identity("user", {"/username": legacy_user}, {})
        records[item.pk] = ApplicationRecord(
            resource_kind=item.resource_kind,
            identity_key=item.identity_key,
            attributes=attributes,
            relationships=relationships,
            display_name=item.display_name,
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
    examples = ", ".join(format_relationship_value(identity) for _, identity in sorted(missing)[:5])
    noun = "dependency" if len(missing) == 1 else "dependencies"
    return [f"The plan references {len(missing)} {noun} absent from both the plan and local NetBox: {examples}."]


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
    examples = "; ".join(_reference_problem_example(requirement, count) for requirement, count in problems[:10])
    remaining = max(0, len(problems) - 10)
    suffix = f", plus {remaining} more" if remaining else ""
    return (
        f"Create or uniquely match {len(problems)} local objects required by the source before applying: "
        f"{examples}{suffix}. Cross-app references are never created implicitly."
    )


def _reference_problem_example(requirement: ReferenceRequirement, count: int) -> str:
    model_name = {
        "users.user": "User",
        "extras.configtemplate": "Config Template",
        "ipam.role": "ASN Role",
    }.get(requirement.model_label, requirement.model_label)
    state = "missing" if count == 0 else f"ambiguous ({count} matches)"
    return f"{model_name} with {requirement.lookup_field} {requirement.value!r} ({state})"


def _content_type_problems(records: list[ApplicationRecord]) -> list[str]:
    content_type_model = apps.get_model("contenttypes.contenttype")
    missing: set[str] = set()
    for record in records:
        values: list[Any] = []
        if record.resource_kind in {"tag", "object_permission"} | CONTENT_TYPE_LIST_KINDS:
            raw_values = record.attributes.get("/object_types", [])
            if not isinstance(raw_values, list):
                raise ApplicationPlanError(f"{record.resource_kind} attribute /object_types must be a list.")
            values.extend(raw_values)
        if record.resource_kind == "custom_field":
            related = record.attributes.get("/related_object_type")
            if related not in (None, ""):
                values.append(related)
        if record.resource_kind == "table_config":
            values.append(record.attributes.get("/object_type"))
        for value in values:
            if not isinstance(value, str) or value.count(".") != 1:
                raise ApplicationPlanError("Object types must use the app_label.model format.")
            app_label, model = value.split(".", 1)
            if not content_type_model.objects.filter(app_label=app_label, model=model).exists():
                missing.add(value)
    if not missing:
        return []
    return [f"The target does not provide {len(missing)} required object types: {', '.join(sorted(missing)[:10])}."]


def _data_source_problems(records: list[ApplicationRecord]) -> list[str]:
    missing_backends: set[str] = set()
    unsafe_urls: list[str] = []
    invalid_parameters: list[str] = []
    for record in records:
        if record.resource_kind != "data_source":
            continue
        backend_type = record.attributes.get("/type")
        if not isinstance(backend_type, str) or not backend_type:
            raise ApplicationPlanError("A data source requires a backend type.")
        if backend_type not in registry["data_backends"]:
            missing_backends.add(backend_type)

        source_url = record.attributes.get("/source_url")
        if not isinstance(source_url, str) or not source_url:
            raise ApplicationPlanError("A data source requires a source URL.")
        try:
            parsed = urlsplit(source_url)
            unsafe = parsed.username is not None or bool(parsed.query) or bool(parsed.fragment)
        except ValueError:
            unsafe = True
        if unsafe:
            unsafe_urls.append(record.identity_key)

        parameters = record.attributes.get("/parameters", {})
        if not isinstance(parameters, dict) or parameters != portable_data_source_parameters(backend_type, parameters):
            invalid_parameters.append(record.identity_key)

    problems: list[str] = []
    if missing_backends:
        problems.append(
            "Install the Data Source backend types required by the source before applying: "
            f"{', '.join(sorted(missing_backends))}."
        )
    if unsafe_urls:
        problems.append(
            f"Remove credentials, query strings, and fragments from {len(unsafe_urls)} Data Source URLs "
            "before applying."
        )
    if invalid_parameters:
        problems.append(
            f"Remove non-portable parameters from {len(invalid_parameters)} Data Sources; "
            "credentials remain destination-local."
        )
    return problems


def _extras_problems(
    records: list[ApplicationRecord],
    target_records: list[CanonicalRecord],
) -> list[str]:
    targets = {(record.resource_kind, record.identity_key): record for record in target_records}
    immutable_type_changes = [
        record.identity_key
        for record in records
        if record.resource_kind == "custom_field"
        and (target := targets.get(record.key)) is not None
        and record.attributes.get("/type") != target.attributes.get("/type")
    ]
    if not immutable_type_changes:
        return []
    return [
        f"Changing the type of {len(immutable_type_changes)} existing Custom Fields is not supported; "
        "create a replacement field instead."
    ]


class _NetBoxMutationBackend:
    """DiffSync mutation backend for the local NetBox ORM."""

    capabilities = AdapterCapabilities(readable=True, writable=True, deletable=False, atomic=True)

    def __init__(
        self,
        items: list[ComparisonItem],
        target_records: list[CanonicalRecord],
        desired_records: list[CanonicalRecord],
        references: dict[ReferenceRequirement, Any],
    ) -> None:
        self.items_by_key = {
            (item.resource_kind, item.identity_key): item
            for item in items
            if item.action in {ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE}
        }
        self.target_by_key = {(record.resource_kind, record.identity_key): record for record in target_records}
        self.desired_by_key = {(record.resource_kind, record.identity_key): record for record in desired_records}
        self.references = references
        self.object_cache: dict[tuple[str, str], Any] = {}
        self.mutable_records: list[ApplicationRecord] = []
        self.materializing: set[tuple[str, str]] = set()

    def create(self, canonical: CanonicalRecord) -> None:
        record = _application_record(canonical)
        if record.key in self.object_cache:
            return
        if record.key in self.target_by_key:
            raise ApplicationPlanError(f"Target identity {record.identity_key} appeared after comparison.")
        self._begin_materializing(record)
        try:
            self._materialize_dependencies(record)
            obj = MODEL_BY_KIND[record.resource_kind]()
            _write_object(obj, record, self.target_by_key, self.object_cache, self.references)
            self.object_cache[record.key] = obj
            self.mutable_records.append(record)
        finally:
            self.materializing.remove(record.key)

    def update(self, canonical: CanonicalRecord) -> None:
        record = _application_record(canonical)
        if record.key in self.object_cache:
            return
        item = self.items_by_key.get(record.key)
        target_record = self.target_by_key.get(record.key)
        if item is None or target_record is None:
            raise ApplicationPlanError(f"Update target {record.identity_key} disappeared after comparison.")
        if (
            item.target_object_type != target_record.target_object_type
            or item.target_object_id != target_record.target_object_id
        ):
            raise ApplicationPlanError(f"Update target {record.identity_key} no longer matches the reviewed object.")
        self._begin_materializing(record)
        try:
            self._materialize_dependencies(record)
            obj = _load_target_object(target_record)
            _write_object(obj, record, self.target_by_key, self.object_cache, self.references)
            self.object_cache[record.key] = obj
            self.mutable_records.append(record)
        finally:
            self.materializing.remove(record.key)

    def delete(self, resource_kind: str, identity_key: str) -> None:
        raise ApplicationPlanError(
            f"Deletion is disabled by policy; refused {resource_kind}:{identity_key}."
        )

    def sync_complete(self) -> None:
        _write_deferred_relationships(
            self.mutable_records,
            self.target_by_key,
            self.object_cache,
        )

    def _begin_materializing(self, record: ApplicationRecord) -> None:
        if record.key in self.materializing:
            raise ApplicationPlanError(
                f"The application dependency graph contains a cycle at {record.resource_kind}:{record.display_name}."
            )
        self.materializing.add(record.key)

    def _materialize_dependencies(self, record: ApplicationRecord) -> None:
        for key in relationship_dependencies(record, include_deferred=False):
            item = self.items_by_key.get(key)
            desired = self.desired_by_key.get(key)
            if item is None or desired is None or key in self.object_cache:
                continue
            if item.action == ComparisonItem.Action.CREATE:
                self.create(desired)
            elif item.action == ComparisonItem.Action.UPDATE:
                self.update(desired)

    def objects_by_item(
        self,
        items: list[ComparisonItem],
        records_by_item: dict[int, ApplicationRecord],
    ) -> dict[int, Any]:
        objects: dict[int, Any] = {}
        for item in items:
            if item.action not in APPLY_ACTIONS:
                continue
            record = records_by_item[item.pk]
            obj = self.object_cache.get(record.key)
            if obj is None:
                target_record = self.target_by_key.get(record.key)
                if target_record is None:
                    raise ApplicationPlanError(f"Reviewed target {record.identity_key} disappeared after comparison.")
                obj = _load_target_object(target_record)
                self.object_cache[record.key] = obj
            objects[item.pk] = obj
        return objects


def _sync_items(
    items: list[ComparisonItem],
    records_by_item: dict[int, ApplicationRecord],
    target_records: list[CanonicalRecord],
    references: dict[ReferenceRequirement, Any],
) -> dict[int, Any]:
    """Execute the reviewed, freshly revalidated delta through DiffSync."""

    records = [records_by_item[item.pk] for item in items if item.action in APPLY_ACTIONS]
    ordered_records = dependency_order(records)
    canonical_by_key = {
        record.key: _canonical_record(record, _item_for_key(items, record.key)) for record in ordered_records
    }
    ordered_canonical = [canonical_by_key[record.key] for record in ordered_records]
    backend = _NetBoxMutationBackend(items, target_records, ordered_canonical, references)
    source_adapter, target_adapter = build_adapter_pair(
        ordered_canonical,
        target_records,
        source_order=ordered_canonical,
        mutation_backend=backend,
    )
    diff = source_adapter.diff_to(target_adapter, flags=NO_DELETE_FLAGS)
    _verify_reviewed_actions(items, diff)
    source_adapter.sync_to(target_adapter, flags=NO_DELETE_FLAGS, diff=diff)
    return backend.objects_by_item(items, records_by_item)


def _verify_reviewed_actions(items: list[ComparisonItem], diff: Any) -> None:
    expected = {
        (item.resource_kind, item.identity_key): item.action
        for item in items
        if item.action in {ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE}
    }
    actual = {
        (str(element.type), str(element.keys["identity_key"])): str(element.action)
        for element in diff.get_children()
        if str(element.action) in {ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE}
    }
    if actual != expected:
        raise ApplicationPlanError(
            "The freshly calculated DiffSync delta no longer matches the reviewed create/update actions."
        )


def _canonical_record(record: ApplicationRecord, item: ComparisonItem) -> CanonicalRecord:
    return CanonicalRecord(
        resource_kind=record.resource_kind,
        identity_key=record.identity_key,
        display_name=item.display_name,
        external_id=item.source_external_id,
        attributes=record.attributes,
        relationships=record.relationships,
    )


def _application_record(record: CanonicalRecord) -> ApplicationRecord:
    return ApplicationRecord(
        resource_kind=record.resource_kind,
        identity_key=record.identity_key,
        attributes=record.attributes,
        relationships=record.relationships,
        display_name=record.display_name,
    )


def _item_for_key(items: list[ComparisonItem], key: tuple[str, str]) -> ComparisonItem:
    matches = [item for item in items if (item.resource_kind, item.identity_key) == key]
    if len(matches) != 1:
        raise ApplicationPlanError(f"Expected one reviewed comparison item for {key[0]}:{key[1]}.")
    return matches[0]


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

    if kind in ATTRIBUTE_FIELDS:
        if (
            kind == "custom_field"
            and not obj._state.adding
            and "/type" in attributes
            and obj.type != attributes["/type"]
        ):
            raise ApplicationPlanError("Changing the type of an existing custom field is not supported.")
        declared_attributes = attributes
        if kind == "virtual_machine" and not obj._state.adding and obj.virtualdisks.exists():
            # VirtualDisk signals own the aggregate once component disks exist.
            declared_attributes = {path: value for path, value in attributes.items() if path != "/disk"}
        _write_declared_fields(obj, kind, declared_attributes)
        for relationship_name, (target_kind, field_name) in RELATIONSHIP_FIELDS[kind].items():
            if _is_deferred_write_relationship(kind, relationship_name):
                continue
            setattr(
                obj,
                field_name,
                _relationship_object(
                    target_kind,
                    relationships.get(relationship_name),
                    target_by_key,
                    object_cache,
                    required=relationship_name in REQUIRED_RELATIONSHIPS.get(kind, frozenset()),
                ),
            )
        if kind == "custom_field":
            obj.related_object_type = _content_type(attributes.get("/related_object_type"))
        elif kind == "table_config":
            obj.object_type = _content_type(attributes.get("/object_type"), required=True)
        elif kind == "event_rule":
            obj.action_object = _generic_relationship_object(
                kind,
                "action_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "module_type":
            obj.attribute_data = attributes.get("/attributes")
        elif kind in {"inventory_item", "inventory_item_template"}:
            obj.component = _generic_relationship_object(
                kind,
                "component_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "mac_address":
            obj.assigned_object = _generic_relationship_object(
                kind,
                "assigned_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind in {"vlan_group", "prefix", "cluster", "wireless_lan"}:
            obj.scope = _generic_relationship_object(
                kind,
                "scope_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "ip_address":
            obj.assigned_object = _generic_relationship_object(
                kind,
                "assigned_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "fhrp_group_assignment":
            obj.interface = _generic_relationship_object(
                kind,
                "interface_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "service":
            obj.parent = _generic_relationship_object(
                kind,
                "parent_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "contact_assignment":
            obj.object = _generic_relationship_object(
                kind,
                "object_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "tunnel_termination":
            obj.termination = _generic_relationship_object(
                kind,
                "termination_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "l2vpn_termination":
            obj.assigned_object = _generic_relationship_object(
                kind,
                "assigned_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "circuit_termination":
            obj.termination = _generic_relationship_object(
                kind,
                "termination_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "circuit_group_assignment":
            obj.member = _generic_relationship_object(
                kind,
                "member_",
                relationships,
                target_by_key,
                object_cache,
            )
        elif kind == "cable":
            obj.a_terminations = _cable_termination_objects("a", relationships, target_by_key, object_cache)
            obj.b_terminations = _cable_termination_objects("b", relationships, target_by_key, object_cache)
        elif kind == "data_source":
            _write_data_source_parameters(obj, attributes)
        if kind in CUSTOM_FIELD_KINDS and "/custom_fields" in attributes:
            _write_custom_field_data(obj, record, target_by_key, object_cache)
    else:
        raise ApplicationPlanError(f"Resource kind {record.resource_kind!r} cannot be written.")

    if kind == "user" and obj._state.adding:
        obj.set_unusable_password()
    if kind in {"export_template", "config_context_profile", "config_context", "config_template"}:
        # The portable graph owns the materialized inline definition, not the
        # source instance's generated DataFile binding or synchronization state.
        obj.data_file = None
    _full_clean_source_object(obj, kind)
    if obj._state.adding and kind in {"device", "module"}:
        super(obj.__class__, obj).save()
    else:
        obj.save()
    if hasattr(obj, "_original_device"):
        obj._original_device = obj.device_id
    if hasattr(obj, "_original_device_type"):
        obj._original_device_type = obj.device_type_id
    if kind == "tag":
        obj.object_types.set(_content_types(attributes.get("/object_types", [])))
    if kind == "object_permission":
        obj.object_types.set(_content_types(attributes.get("/object_types", [])))
    if kind in CONTENT_TYPE_LIST_KINDS:
        obj.object_types.set(_content_types(attributes.get("/object_types", [])))
    if kind == "owner":
        obj.user_groups.set(
            _relationship_objects("user_group", relationships.get("user_group"), target_by_key, object_cache)
        )
        obj.users.set(_relationship_objects("user", relationships.get("user"), target_by_key, object_cache))
    if kind == "user_group":
        obj.object_permissions.set(
            _relationship_objects(
                "object_permission",
                relationships.get("permission"),
                target_by_key,
                object_cache,
            )
        )
    if kind == "user":
        obj.groups.set(_relationship_objects("user_group", relationships.get("group"), target_by_key, object_cache))
        obj.object_permissions.set(
            _relationship_objects(
                "object_permission",
                relationships.get("permission"),
                target_by_key,
                object_cache,
            )
        )
    if kind == "site":
        obj.asns.set(_relationship_objects("asn", relationships.get("asn"), target_by_key, object_cache))
    if kind == "provider":
        obj.asns.set(_relationship_objects("asn", relationships.get("asn"), target_by_key, object_cache))
    if kind == "vrf":
        obj.import_targets.set(
            _relationship_objects("route_target", relationships.get("import_target"), target_by_key, object_cache)
        )
        obj.export_targets.set(
            _relationship_objects("route_target", relationships.get("export_target"), target_by_key, object_cache)
        )
    if kind == "service":
        obj.ipaddresses.set(
            _relationship_objects("ip_address", relationships.get("ip_address"), target_by_key, object_cache)
        )
    if kind == "contact":
        obj.groups.set(
            _relationship_objects("contact_group", relationships.get("group"), target_by_key, object_cache)
        )
    if kind == "vm_interface":
        obj.tagged_vlans.set(
            _relationship_objects("vlan", relationships.get("tagged_vlan"), target_by_key, object_cache)
        )
    if kind == "ike_policy":
        obj.proposals.set(
            _relationship_objects("ike_proposal", relationships.get("proposal"), target_by_key, object_cache)
        )
    if kind == "ipsec_policy":
        obj.proposals.set(
            _relationship_objects("ipsec_proposal", relationships.get("proposal"), target_by_key, object_cache)
        )
    if kind == "l2vpn":
        obj.import_targets.set(
            _relationship_objects("route_target", relationships.get("import_target"), target_by_key, object_cache)
        )
        obj.export_targets.set(
            _relationship_objects("route_target", relationships.get("export_target"), target_by_key, object_cache)
        )
    if kind == "config_context":
        for name, target_kind in CONFIG_CONTEXT_MULTI_RELATIONSHIPS.items():
            getattr(obj, f"{name}s").set(
                _relationship_objects(target_kind, relationships.get(name), target_by_key, object_cache)
            )
    if kind == "notification_group":
        obj.groups.set(_relationship_objects("user_group", relationships.get("group"), target_by_key, object_cache))
        obj.users.set(_relationship_objects("user", relationships.get("user"), target_by_key, object_cache))
    if kind in TAGGED_KINDS:
        obj.tags.set(_relationship_objects("tag", relationships.get("tag"), target_by_key, object_cache))
    if kind == "interface":
        obj.vdcs.set(
            _relationship_objects(
                "virtual_device_context",
                relationships.get("vdc"),
                target_by_key,
                object_cache,
            )
        )
        obj.tagged_vlans.set(
            _relationship_objects("vlan", relationships.get("tagged_vlan"), target_by_key, object_cache)
        )
        if attributes.get("/manage_wireless_lans") is True:
            obj.wireless_lans.set(
                _relationship_objects("wireless_lan", relationships.get("wireless_lan"), target_by_key, object_cache)
            )


def _full_clean_source_object(obj: Any, resource_kind: str) -> None:
    try:
        obj.full_clean()
    except ValidationError as exc:
        if resource_kind != "user" or set(exc.message_dict) != {"username"}:
            raise
        username = obj.username
        username_field = obj._meta.get_field("username")
        if (
            not isinstance(username, str)
            or not username
            or "\x00" in username
            or (username_field.max_length is not None and len(username) > username_field.max_length)
        ):
            raise
        # NetBox installations can contain usernames grandfathered from an older
        # validator. Preserve the source identity exactly; all other field,
        # model, uniqueness, and database constraints remain enforced.
        obj.full_clean(exclude={"username"})


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


def _write_declared_fields(obj: Any, resource_kind: str, attributes: dict[str, Any]) -> None:
    for field_name in ATTRIBUTE_FIELDS[resource_kind]:
        path = f"/{field_name}"
        if path in attributes:
            value = attributes[path]
            if resource_kind == "vlan_group" and field_name == "vid_ranges":
                try:
                    ranges = normalize_vlan_ranges(value)
                except ValueError as exc:
                    raise ApplicationPlanError(str(exc)) from exc
                value = [NumericRange(item["start"], item["end"] + 1, bounds="[)") for item in ranges]
            setattr(obj, field_name, value)
            continue
        field = obj._meta.get_field(field_name)
        if field.has_default():
            setattr(obj, field_name, field.get_default())
        elif field.null:
            setattr(obj, field_name, None)
        elif field.get_internal_type() in {"CharField", "TextField"}:
            setattr(obj, field_name, "")


def _is_deferred_write_relationship(resource_kind: str, relationship_name: str) -> bool:
    return (
        (resource_kind == "virtual_chassis" and relationship_name == "master")
        or (resource_kind == "interface" and relationship_name == "primary_mac_address")
        or (
            resource_kind in {"device", "virtual_device_context", "virtual_machine"}
            and relationship_name in {"primary_ip4", "primary_ip6", "oob_ip"}
        )
        or (resource_kind == "vm_interface" and relationship_name == "primary_mac_address")
    )


def _write_custom_field_data(
    obj: Any,
    record: ApplicationRecord,
    target_by_key: dict[tuple[str, str], CanonicalRecord],
    object_cache: dict[tuple[str, str], Any],
) -> None:
    desired = record.attributes.get("/custom_fields")
    if not isinstance(desired, dict):
        raise ApplicationPlanError(f"{record.resource_kind} attribute /custom_fields must be an object.")
    custom_field_model = apps.get_model("extras.customfield")
    definitions = {
        custom_field.name: custom_field
        for custom_field in custom_field_model.objects.get_for_model(obj).select_related("related_object_type")
    }
    unknown = sorted(set(desired) - set(definitions))
    if unknown:
        raise ApplicationPlanError(
            f"{record.resource_kind} references unavailable custom fields: {', '.join(unknown[:10])}."
        )

    values: dict[str, Any] = {}
    for field_name, value in desired.items():
        custom_field = definitions[field_name]
        matching = [
            (name, parsed)
            for name in record.relationships
            if (parsed := parse_custom_field_relationship(name)) is not None and parsed[2] == field_name
        ]
        if custom_field.type not in {"object", "multiobject"}:
            if matching:
                raise ApplicationPlanError(f"Custom field {field_name!r} cannot contain object relationships.")
            values[field_name] = value
            continue

        related_model = custom_field.related_object_type.model_class() if custom_field.related_object_type else None
        target_kind = next(
            (kind for kind, model in MODEL_BY_KIND.items() if related_model is not None and model is related_model),
            None,
        )
        if target_kind is None:
            if value in (None, [], "") and not matching:
                values[field_name] = None
                continue
            raise ApplicationPlanError(f"Custom field {field_name!r} targets an unsupported object type.")
        multi = custom_field.type == "multiobject"
        expected_name = custom_field_relationship_name(field_name, target_kind, multi=multi)
        if any(name != expected_name for name, _ in matching):
            raise ApplicationPlanError(f"Custom field {field_name!r} contains an incompatible object relationship.")
        if multi:
            targets = _relationship_objects(
                target_kind,
                record.relationships.get(expected_name),
                target_by_key,
                object_cache,
            )
            values[field_name] = [target.pk for target in targets] or None
        else:
            target = _relationship_object(
                target_kind,
                record.relationships.get(expected_name),
                target_by_key,
                object_cache,
            )
            values[field_name] = target.pk if target is not None else None
    obj.custom_field_data = values


def _write_data_source_parameters(obj: Any, attributes: dict[str, Any]) -> None:
    desired = attributes.get("/parameters", {})
    if not isinstance(desired, dict):
        raise ApplicationPlanError("Data Source attribute /parameters must be an object.")
    portable = portable_data_source_parameters(obj.type, desired)
    if desired != portable:
        raise ApplicationPlanError("Data Source parameters contain non-portable or credential-bearing values.")

    current = dict(obj.parameters) if isinstance(obj.parameters, dict) else {}
    managed_keys = set().union(*PORTABLE_DATA_SOURCE_PARAMETER_KEYS.values())
    for key in managed_keys:
        current.pop(key, None)
    current.update(portable)
    obj.parameters = current or None


def _generic_relationship_object(
    resource_kind: str,
    prefix: str,
    relationships: dict[str, Any],
    target_by_key: dict[tuple[str, str], CanonicalRecord],
    object_cache: dict[tuple[str, str], Any],
) -> Any | None:
    matches = [(name, value) for name, value in relationships.items() if name.startswith(prefix)]
    if not matches:
        return None
    if len(matches) != 1:
        raise ApplicationPlanError(f"{resource_kind} must contain at most one {prefix.rstrip('_')} relationship.")
    name, value = matches[0]
    target_kind = relationship_target(resource_kind, name)
    if target_kind is None:
        raise ApplicationPlanError(f"Relationship {name} on {resource_kind} is unsupported.")
    return _relationship_object(target_kind, value, target_by_key, object_cache, required=True)


def _cable_termination_objects(
    side: str,
    relationships: dict[str, Any],
    target_by_key: dict[tuple[str, str], CanonicalRecord],
    object_cache: dict[tuple[str, str], Any],
) -> list[Any]:
    objects: list[Any] = []
    prefix = f"termination_{side}_"
    for name, identities in sorted(relationships.items()):
        if not name.startswith(prefix):
            continue
        target_kind = relationship_target("cable", name)
        if target_kind is None:
            raise ApplicationPlanError(f"Cable relationship {name} is unsupported.")
        objects.extend(_relationship_objects(target_kind, identities, target_by_key, object_cache))
    return objects


def _write_deferred_relationships(
    records: list[ApplicationRecord],
    target_by_key: dict[tuple[str, str], CanonicalRecord],
    object_cache: dict[tuple[str, str], Any],
) -> None:
    for record in records:
        obj = object_cache[record.key]
        if record.resource_kind == "virtual_chassis":
            obj.master = _relationship_object(
                "device",
                record.relationships.get("master"),
                target_by_key,
                object_cache,
            )
            obj.full_clean()
            obj.save()
        elif record.resource_kind == "interface":
            obj.primary_mac_address = _relationship_object(
                "mac_address",
                record.relationships.get("primary_mac_address"),
                target_by_key,
                object_cache,
            )
            obj.full_clean()
            obj.save()
        elif (
            record.resource_kind in {"device", "virtual_device_context", "virtual_machine"}
            and record.attributes.get("/manage_primary_ip_selectors") is True
        ):
            selector_names = ["primary_ip4", "primary_ip6"]
            if record.resource_kind == "device":
                selector_names.append("oob_ip")
            for selector_name in selector_names:
                setattr(
                    obj,
                    selector_name,
                    _relationship_object(
                        "ip_address",
                        record.relationships.get(selector_name),
                        target_by_key,
                        object_cache,
                    ),
                )
            obj.full_clean()
            obj.save()
        elif record.resource_kind == "vm_interface" and record.attributes.get("/manage_primary_mac_selector") is True:
            obj.primary_mac_address = _relationship_object(
                "mac_address",
                record.relationships.get("primary_mac_address"),
                target_by_key,
                object_cache,
            )
            obj.full_clean()
            obj.save()
        elif record.resource_kind in {"front_port", "front_port_template"}:
            mapping_model = apps.get_model(
                "dcim.porttemplatemapping" if record.resource_kind.endswith("_template") else "dcim.portmapping"
            )
            target_kind = "rear_port_template" if record.resource_kind.endswith("_template") else "rear_port"
            obj.mappings.all().delete()
            for name, identity in sorted(record.relationships.items()):
                if not name.startswith("mapping_"):
                    continue
                try:
                    front_position, rear_position = (int(value) for value in name.removeprefix("mapping_").split("_"))
                except (TypeError, ValueError) as exc:
                    raise ApplicationPlanError(f"Invalid port mapping relationship {name!r}.") from exc
                mapping = mapping_model(
                    front_port=obj,
                    rear_port=_relationship_object(
                        target_kind,
                        identity,
                        target_by_key,
                        object_cache,
                        required=True,
                    ),
                    front_port_position=front_position,
                    rear_port_position=rear_position,
                )
                if record.resource_kind.endswith("_template"):
                    mapping.device_type = obj.device_type
                    mapping.module_type = obj.module_type
                else:
                    mapping.device = obj.device
                mapping.full_clean()
                mapping.save()


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
        raise ApplicationPlanError("Attribute /object_types must be a list.")
    content_type_model = apps.get_model("contenttypes.contenttype")
    content_types: list[Any] = []
    for value in values:
        if not isinstance(value, str) or value.count(".") != 1:
            raise ApplicationPlanError("Object types must use the app_label.model format.")
        app_label, model = value.split(".", 1)
        content_types.append(content_type_model.objects.get(app_label=app_label, model=model))
    return content_types


def _content_type(value: Any, *, required: bool = False) -> Any | None:
    if value in (None, ""):
        if required:
            raise ApplicationPlanError("A content type is required.")
        return None
    values = _content_types([value])
    return values[0]


def _load_target_object(record: CanonicalRecord) -> Any:
    model = apps.get_model(record.target_object_type)
    return model.objects.select_for_update().get(pk=record.target_object_id)


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
