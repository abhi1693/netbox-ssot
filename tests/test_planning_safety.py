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
    normalize_relationship_cardinality,
)
from netbox_ssot.planning.core import portable_data_source_parameters
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


def test_dcim_catalog_and_rack_identities_follow_netbox_uniqueness_context() -> None:
    manufacturer = natural_identity("manufacturer", {"/slug": "acme"}, {})
    platform = natural_identity("platform", {"/slug": "network-os"}, {"manufacturer": manufacturer})
    root_role = natural_identity("device_role", {"/slug": "network"}, {})
    child_role = natural_identity("device_role", {"/slug": "leaf"}, {"parent": root_role})
    device_type = natural_identity(
        "device_type",
        {"/model": "Switch 48"},
        {"manufacturer": manufacturer},
    )
    site = natural_identity("site", {"/slug": "dc1"}, {})
    location = natural_identity("location", {"/slug": "room"}, {"site": site})
    rack_type = natural_identity("rack_type", {"/model": "R42"}, {"manufacturer": manufacturer})
    rack = natural_identity("rack", {"/name": "A01"}, {"site": site, "location": location})

    assert platform != natural_identity("platform", {"/slug": "network-os"}, {})
    assert child_role != natural_identity("device_role", {"/slug": "leaf"}, {})
    assert device_type
    assert rack_type
    assert rack != natural_identity("rack", {"/name": "A01"}, {"site": site})


def test_unsupported_cross_app_generic_assignments_and_cable_terminations_fail_closed() -> None:
    with pytest.raises(ValueError, match="MAC address assignment"):
        natural_identity(
            "mac_address",
            {"/mac_address": "00:11:22:33:44:55", "/assigned_object_type": "virtualization.vminterface"},
            {},
        )

    with pytest.raises(ValueError, match=r"wireless\.wirelesslink"):
        natural_identity(
            "cable",
            {"/unsupported_termination_types": ["wireless.wirelesslink"]},
            {"termination_a_interface": ["interface"]},
        )


def test_circuit_identities_follow_netbox_uniqueness_context() -> None:
    provider = natural_identity("provider", {"/slug": "carrier"}, {})
    account = natural_identity(
        "provider_account", {"/account": "1234"}, {"provider": provider}
    )
    network = natural_identity(
        "provider_network", {"/name": "backbone"}, {"provider": provider}
    )
    circuit_type = natural_identity("circuit_type", {"/slug": "transit"}, {})
    circuit = natural_identity(
        "circuit",
        {"/cid": "CID-1"},
        {"provider": provider, "provider_account": account, "type": circuit_type},
    )
    termination = natural_identity(
        "circuit_termination",
        {"/term_side": "A"},
        {"circuit": circuit, "termination_site": "site"},
    )
    virtual_type = natural_identity("virtual_circuit_type", {"/slug": "evpn"}, {})
    virtual_circuit = natural_identity(
        "virtual_circuit",
        {"/cid": "VC-1"},
        {"provider_network": network, "type": virtual_type},
    )
    group = natural_identity("circuit_group", {"/slug": "wan"}, {})

    assert termination == natural_identity(
        "circuit_termination",
        {"/term_side": "A"},
        {"circuit": circuit, "termination_provider_network": network},
    )
    assert natural_identity(
        "virtual_circuit_termination",
        {},
        {"virtual_circuit": virtual_circuit, "interface": "interface"},
    )
    assert natural_identity(
        "circuit_group_assignment",
        {},
        {"group": group, "member_circuit": circuit},
    )
    assert natural_identity(
        "cable",
        {},
        {"termination_a_circuit_termination": [termination]},
    )

    with pytest.raises(ValueError, match="exactly one supported member"):
        natural_identity("circuit_group_assignment", {}, {"group": group})


def test_user_identities_are_portable_without_credentials() -> None:
    assert natural_identity("object_permission", {"/name": "View sites"}, {})
    assert natural_identity("user_group", {"/name": "Network operators"}, {})
    assert natural_identity("user", {"/username": "Alice"}, {}) == natural_identity(
        "user", {"/username": "alice"}, {}
    )

    with pytest.raises(ValueError, match="username"):
        natural_identity("user", {}, {})


def test_data_source_identity_and_parameters_exclude_destination_credentials() -> None:
    assert natural_identity("data_source", {"/name": "Automation"}, {})
    assert portable_data_source_parameters(
        "git",
        {
            "branch": "production",
            "username": "source-user",
            "password": "source-password",
        },
    ) == {"branch": "production"}
    assert portable_data_source_parameters(
        "amazon-s3",
        {"aws_access_key_id": "key", "aws_secret_access_key": "secret"},
    ) == {}

    with pytest.raises(ValueError, match="name"):
        natural_identity("data_source", {}, {})


def test_relationship_cardinality_preserves_single_many_to_many_values() -> None:
    assert normalize_relationship_cardinality("rack", {"tag": ["managed"], "site": ["dc1"]}) == {
        "site": "dc1",
        "tag": ["managed"],
    }

    with pytest.raises(ValueError, match="Scalar relationship"):
        normalize_relationship_cardinality("rack", {"site": ["dc1", "dc2"]})

    assert normalize_relationship_cardinality("provider", {"asn": ["64512"]}) == {"asn": ["64512"]}
    assert normalize_relationship_cardinality(
        "user", {"group": ["operators"], "permission": ["view-sites"]}
    ) == {"group": ["operators"], "permission": ["view-sites"]}


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
