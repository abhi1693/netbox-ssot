from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, Self, cast

from diffsync import Adapter, DiffSyncModel
from diffsync.enum import DiffSyncFlags

from .dcim import ATTRIBUTE_FIELDS, EXTRA_ATTRIBUTE_FIELDS, RELATIONSHIP_FIELDS, TAGGED_KINDS

if TYPE_CHECKING:
    from .comparison import CanonicalRecord

NO_DELETE_FLAGS = DiffSyncFlags.SKIP_UNMATCHED_DST


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    readable: bool = True
    writable: bool = False
    deletable: bool = False
    atomic: bool = False


READ_ONLY_CAPABILITIES = AdapterCapabilities()


class MutationBackend(Protocol):
    """Destination-specific operations invoked by DiffSync model hooks."""

    capabilities: AdapterCapabilities

    def create(self, record: CanonicalRecord) -> None: ...

    def update(self, record: CanonicalRecord) -> None: ...

    def delete(self, resource_kind: str, identity_key: str) -> None: ...

    def sync_complete(self) -> None: ...


class ManagedDiffSyncModel(DiffSyncModel):
    """Typed canonical model whose mutations are delegated to its target adapter."""

    _identifiers: ClassVar[tuple[str, ...]] = ("identity_key",)
    _canonical_fields: ClassVar[dict[str, tuple[str, str]]] = {}

    identity_key: str

    @classmethod
    def create(cls, adapter: Adapter, ids: dict[str, Any], attrs: dict[str, Any]) -> Self | None:
        cast(CanonicalAdapter, adapter).apply_create(cls._modelname, str(ids["identity_key"]))
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]) -> Self | None:
        cast(CanonicalAdapter, self.adapter).apply_update(self._modelname, self.identity_key)
        return super().update(attrs)

    def delete(self) -> Self | None:
        cast(CanonicalAdapter, self.adapter).apply_delete(self._modelname, self.identity_key)
        return super().delete()


class CanonicalAdapter(Adapter):
    """Adapter populated from canonical records and optionally backed by mutations."""

    top_level: ClassVar[list[str]] = []

    def __init__(
        self,
        records: Iterable[CanonicalRecord],
        *,
        desired_records: Mapping[tuple[str, str], CanonicalRecord] | None = None,
        mutation_backend: MutationBackend | None = None,
    ) -> None:
        super().__init__()
        self.desired_records = dict(desired_records or {})
        self.mutation_backend = mutation_backend
        for record in records:
            model_class = cast(type[ManagedDiffSyncModel], getattr(self, record.resource_kind))
            self.add(model_class(**_model_values(model_class, record)))

    def apply_create(self, resource_kind: str, identity_key: str) -> None:
        self._backend().create(self._desired_record(resource_kind, identity_key))

    def apply_update(self, resource_kind: str, identity_key: str) -> None:
        self._backend().update(self._desired_record(resource_kind, identity_key))

    def apply_delete(self, resource_kind: str, identity_key: str) -> None:
        self._backend().delete(resource_kind, identity_key)

    def sync_complete(
        self,
        source: Adapter,
        diff: Any,
        flags: DiffSyncFlags = DiffSyncFlags.NONE,
        logger: Any | None = None,
    ) -> None:
        del source, diff, flags, logger
        self._backend().sync_complete()

    def _backend(self) -> MutationBackend:
        if self.mutation_backend is None:
            raise RuntimeError("This canonical adapter has no mutation backend.")
        if not self.mutation_backend.capabilities.writable:
            raise RuntimeError("This canonical adapter does not advertise write capability.")
        return self.mutation_backend

    @property
    def capabilities(self) -> AdapterCapabilities:
        return self.mutation_backend.capabilities if self.mutation_backend else READ_ONLY_CAPABILITIES

    def _desired_record(self, resource_kind: str, identity_key: str) -> CanonicalRecord:
        try:
            return self.desired_records[(resource_kind, identity_key)]
        except KeyError as exc:
            raise RuntimeError(f"No desired record exists for {resource_kind}:{identity_key}.") from exc


def build_adapter_pair(
    source_records: Sequence[CanonicalRecord],
    target_records: Sequence[CanonicalRecord],
    *,
    source_order: Sequence[CanonicalRecord] | None = None,
    mutation_backend: MutationBackend | None = None,
) -> tuple[CanonicalAdapter, CanonicalAdapter]:
    """Build source and target adapters sharing one typed model registry."""

    records = [*source_records, *target_records]
    model_classes = _model_classes(records)
    kind_order = _kind_order(source_order or source_records, model_classes)
    namespace: dict[str, Any] = {**model_classes, "top_level": kind_order}
    adapter_class = cast(type[CanonicalAdapter], type("CanonicalModelAdapter", (CanonicalAdapter,), namespace))
    desired = {(record.resource_kind, record.identity_key): record for record in source_records}
    source = adapter_class(source_order or source_records)
    target = adapter_class(target_records, desired_records=desired, mutation_backend=mutation_backend)
    return source, target


def _model_classes(records: Sequence[CanonicalRecord]) -> dict[str, type[ManagedDiffSyncModel]]:
    fields_by_kind: dict[str, set[tuple[str, str]]] = {}
    for record in records:
        fields = fields_by_kind.setdefault(record.resource_kind, _declared_fields(record.resource_kind))
        fields.update(("attributes", name) for name in record.attributes)
        fields.update(("relationships", name) for name in record.relationships)

    classes: dict[str, type[ManagedDiffSyncModel]] = {}
    for resource_kind, canonical_fields in sorted(fields_by_kind.items()):
        field_map: dict[str, tuple[str, str]] = {}
        for category, name in sorted(canonical_fields):
            field_name = _field_name(category, name)
            if field_name in field_map and field_map[field_name] != (category, name):
                raise ValueError(f"Canonical field name collision on {resource_kind}:{category}:{name}.")
            field_map[field_name] = (category, name)
        annotations: dict[str, Any] = dict.fromkeys(field_map, tuple[bool, Any])
        annotations.update(
            {
                "_modelname": ClassVar[str],
                "_attributes": ClassVar[tuple[str, ...]],
                "_canonical_fields": ClassVar[dict[str, tuple[str, str]]],
            }
        )
        namespace = {
            "__annotations__": annotations,
            "_modelname": resource_kind,
            "_attributes": tuple(field_map),
            "_canonical_fields": field_map,
        }
        class_name = "".join(part.title() for part in resource_kind.split("_")) + "Model"
        classes[resource_kind] = cast(
            type[ManagedDiffSyncModel],
            type(class_name, (ManagedDiffSyncModel,), namespace),
        )
    return classes


def _declared_fields(resource_kind: str) -> set[tuple[str, str]]:
    attribute_names = (*ATTRIBUTE_FIELDS.get(resource_kind, ()), *EXTRA_ATTRIBUTE_FIELDS.get(resource_kind, ()))
    relationship_names = set(RELATIONSHIP_FIELDS.get(resource_kind, {}))
    if resource_kind in TAGGED_KINDS:
        relationship_names.add("tag")
    if resource_kind == "site":
        relationship_names.add("asn")
    elif resource_kind == "interface":
        relationship_names.add("vdc")
    return {
        *(("attributes", f"/{name}") for name in attribute_names),
        *(("relationships", name) for name in relationship_names),
    }


def _model_values(model_class: type[ManagedDiffSyncModel], record: CanonicalRecord) -> dict[str, Any]:
    values: dict[str, Any] = {"identity_key": record.identity_key}
    payload = record.payload
    for field_name, (category, name) in model_class._canonical_fields.items():
        fields = payload[category]
        values[field_name] = (name in fields, fields.get(name))
    return values


def _field_name(category: str, name: str) -> str:
    prefix = "attr" if category == "attributes" else "rel"
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip("/"))
    if not normalized or normalized[0].isdigit():
        normalized = f"field_{normalized}"
    return f"{prefix}_{normalized}"


def _kind_order(
    ordered_records: Sequence[CanonicalRecord],
    model_classes: Mapping[str, type[ManagedDiffSyncModel]],
) -> list[str]:
    kinds = list(dict.fromkeys(record.resource_kind for record in ordered_records))
    kinds.extend(kind for kind in sorted(model_classes) if kind not in kinds)
    return kinds
