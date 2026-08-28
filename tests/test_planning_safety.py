from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from diffsync.enum import DiffSyncFlags
from pydantic import ValidationError

from netbox_ssot.planning import ComparisonOnlyDiffSyncEngine
from netbox_ssot.planning.comparison import (
    CanonicalRecord,
    ComparisonAction,
    compare_canonical_records,
    natural_identity,
)
from netbox_ssot_contracts import (
    ChangeAction,
    ChangeProposal,
    Evidence,
    ReconciliationPlan,
    ResourceKind,
    SafetyPolicy,
)


class SpyAdapter:
    def __init__(self) -> None:
        self.flags = DiffSyncFlags.NONE
        self.target: object | None = None
        self.result = object()

    def diff_to(self, target: object, *, flags: DiffSyncFlags) -> object:
        self.target = target
        self.flags = flags
        return self.result


def test_diffsync_gateway_is_comparison_only_and_skips_destination_only_records() -> None:
    engine = ComparisonOnlyDiffSyncEngine()
    source = SpyAdapter()
    target = object()

    result = engine.compare(source, target)  # type: ignore[arg-type]

    assert result is source.result
    assert source.target is target
    assert source.flags == DiffSyncFlags.SKIP_UNMATCHED_DST
    assert not hasattr(engine, "sync")
    assert not hasattr(engine, "sync_to")


def canonical_record(identity: str, name: str, *, target: bool = False) -> CanonicalRecord:
    return CanonicalRecord(
        resource_kind="site",
        identity_key=identity,
        display_name=name,
        external_id=f"source:{identity}",
        attributes={"/name": name, "/slug": identity},
        relationships={},
        target_object_type="dcim.site" if target else "",
        target_object_id="1" if target else "",
    )


def test_canonical_comparison_reports_create_update_and_no_change_without_target_deletes() -> None:
    source = [
        canonical_record("create", "Create"),
        canonical_record("update", "New name"),
        canonical_record("same", "Same"),
    ]
    target = [
        canonical_record("update", "Old name", target=True),
        canonical_record("same", "Same", target=True),
        canonical_record("target-only", "Target only", target=True),
    ]

    results = compare_canonical_records(source, target)

    assert {result.source.identity_key: result.action for result in results} == {
        "create": ComparisonAction.CREATE,
        "update": ComparisonAction.UPDATE,
        "same": ComparisonAction.NO_CHANGE,
    }
    update = next(result for result in results if result.action is ComparisonAction.UPDATE)
    assert update.changes == (
        {
            "field": "attributes:/name",
            "source_present": True,
            "source_value": "New name",
            "target_present": True,
            "target_value": "Old name",
        },
    )


def test_natural_identity_requires_relationship_context_for_locations() -> None:
    site = natural_identity("site", {"/slug": "dc1"}, {})
    assert natural_identity("location", {"/slug": "room"}, {"site": site})

    with pytest.raises(ValueError, match="site"):
        natural_identity("location", {"/slug": "room"}, {})


def test_region_and_location_identities_include_hierarchy_context() -> None:
    root_region = natural_identity("region", {"/slug": "north"}, {})
    child_region = natural_identity("region", {"/slug": "city"}, {"parent": root_region})
    site = natural_identity("site", {"/slug": "dc1"}, {})
    building = natural_identity("location", {"/slug": "building"}, {"site": site})
    room = natural_identity(
        "location",
        {"/slug": "room"},
        {"site": site, "parent": building},
    )

    assert child_region != natural_identity("region", {"/slug": "city"}, {})
    assert room != natural_identity("location", {"/slug": "room"}, {"site": site})


def test_supporting_model_identities_preserve_hierarchy_and_scope() -> None:
    tenant_group = natural_identity("tenant_group", {"/slug": "customers"}, {})
    tenant = natural_identity("tenant", {"/slug": "acme"}, {"group": tenant_group})
    site_group = natural_identity("site_group", {"/slug": "branches"}, {})

    assert tenant != natural_identity("tenant", {"/slug": "acme"}, {})
    assert site_group == natural_identity("site_group", {"/slug": "branches"}, {})
    assert natural_identity("tag", {"/slug": "managed"}, {})
    assert natural_identity("owner_group", {"/name": "Infrastructure"}, {})
    assert natural_identity("owner", {"/name": "Network Team"}, {})
    assert natural_identity("rir", {"/slug": "private"}, {})
    assert natural_identity("asn", {"/asn": 64512}, {})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("skip_unmatched_destination", False, "destination-only records"),
        ("allow_hard_delete", True, "hard deletion"),
        ("allow_automatic_fuzzy_match", True, "automatic fuzzy identity"),
    ],
)
def test_v1_safety_policy_fails_closed(field: str, value: bool, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SafetyPolicy.model_validate({field: value})


def test_plan_rejects_hard_delete_even_when_change_is_explicitly_destructive() -> None:
    now = datetime.now(UTC)
    delete = ChangeProposal(
        change_id=uuid4(),
        action=ChangeAction.DELETE,
        resource_kind=ResourceKind.DEVICE,
        external_id="device:abc123",
        target_object_id="42",
        target_fingerprint="0" * 64,
        destructive=True,
        evidence=(
            Evidence(
                source_object_type="absence_window",
                source_object_id="device:abc123",
                note="Two complete snapshots did not contain this object.",
                observed_at=now,
            ),
        ),
    )

    with pytest.raises(ValidationError, match="cannot contain hard-delete"):
        ReconciliationPlan(
            plan_id=uuid4(),
            source_id=uuid4(),
            run_ids=(uuid4(),),
            created_at=now,
            target_snapshot_fingerprint="1" * 64,
            changes=(delete,),
        )
