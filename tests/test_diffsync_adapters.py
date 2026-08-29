from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from netbox_ssot.planning.adapters import NO_DELETE_FLAGS, AdapterCapabilities, build_adapter_pair
from netbox_ssot.planning.comparison import CanonicalRecord


def record(kind: str, identity: str, name: str) -> CanonicalRecord:
    return CanonicalRecord(
        resource_kind=kind,
        identity_key=identity,
        display_name=name,
        external_id=f"source:{kind}:{identity}",
        attributes={"/name": name},
        relationships={},
    )


@dataclass
class RecordingBackend:
    capabilities: ClassVar[AdapterCapabilities] = AdapterCapabilities(
        readable=True,
        writable=True,
        deletable=False,
        atomic=True,
    )
    operations: list[tuple[str, str, str]] = field(default_factory=list)
    completed: bool = False

    def create(self, item: CanonicalRecord) -> None:
        self.operations.append(("create", item.resource_kind, item.identity_key))

    def update(self, item: CanonicalRecord) -> None:
        self.operations.append(("update", item.resource_kind, item.identity_key))

    def delete(self, resource_kind: str, identity_key: str) -> None:
        self.operations.append(("delete", resource_kind, identity_key))

    def sync_complete(self) -> None:
        self.completed = True


def test_typed_models_drive_reviewed_sync_to_without_deleting_target_only_records() -> None:
    source_records = [record("site", "new", "New"), record("site", "changed", "New name")]
    target_records = [record("site", "changed", "Old name"), record("site", "target-only", "Target only")]
    backend = RecordingBackend()
    source, target = build_adapter_pair(source_records, target_records, mutation_backend=backend)

    assert source.site._modelname == "site"
    assert source.site._identifiers == ("identity_key",)
    assert "attr_name" in source.site._attributes
    assert "rel_tag" in source.site._attributes
    assert "attributes" not in source.site._attributes

    reviewed_diff = source.diff_to(target, flags=NO_DELETE_FLAGS)
    source.sync_to(target, flags=NO_DELETE_FLAGS, diff=reviewed_diff)

    assert backend.operations == [("create", "site", "new"), ("update", "site", "changed")]
    assert backend.completed
    assert target.get(source.site, "new").attr_name == (True, "New")
    assert target.get(source.site, "changed").attr_name == (True, "New name")


def test_the_same_adapter_contract_supports_the_reverse_direction() -> None:
    system_a = [record("site", "shared", "System A")]
    system_b = [record("site", "shared", "System B")]
    backend = RecordingBackend()
    source, target = build_adapter_pair(system_b, system_a, mutation_backend=backend)

    reviewed_diff = source.diff_to(target, flags=NO_DELETE_FLAGS)
    source.sync_to(target, flags=NO_DELETE_FLAGS, diff=reviewed_diff)

    assert backend.operations == [("update", "site", "shared")]
    assert target.get(source.site, "shared").attr_name == (True, "System B")
