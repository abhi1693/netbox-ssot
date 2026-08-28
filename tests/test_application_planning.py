from __future__ import annotations

import pytest

from netbox_ssot.application.planning import (
    ApplicationPlanError,
    ApplicationRecord,
    ReferenceRequirement,
    dependency_order,
    external_reference_requirements,
)


def record(
    resource_kind: str,
    identity: str,
    *,
    attributes: dict[str, object] | None = None,
    relationships: dict[str, object] | None = None,
) -> ApplicationRecord:
    return ApplicationRecord(
        resource_kind=resource_kind,
        identity_key=identity,
        attributes=attributes or {},
        relationships=relationships or {},
    )


def test_dependency_order_places_region_site_and_location_parents_first() -> None:
    records = [
        record("location", "room", relationships={"site": "site", "parent": "building"}),
        record("site", "site", relationships={"region": "child-region"}),
        record("region", "child-region", relationships={"parent": "root-region"}),
        record("location", "building", relationships={"site": "site"}),
        record("region", "root-region"),
    ]

    ordered = dependency_order(records)

    positions = {item.key: index for index, item in enumerate(ordered)}
    assert positions[("region", "root-region")] < positions[("region", "child-region")]
    assert positions[("region", "child-region")] < positions[("site", "site")]
    assert positions[("site", "site")] < positions[("location", "building")]
    assert positions[("location", "building")] < positions[("location", "room")]


def test_dependency_order_places_supporting_models_before_consumers() -> None:
    records = [
        record(
            "site",
            "site",
            relationships={"group": "site-group", "tenant": "tenant", "asn": ["asn"], "tag": ["tag"]},
        ),
        record("asn", "asn", relationships={"rir": "rir", "tenant": "tenant", "tag": ["tag"]}),
        record("tenant", "tenant", relationships={"group": "tenant-group", "tag": ["tag"]}),
        record("tenant_group", "tenant-group", relationships={"tag": ["tag"]}),
        record("site_group", "site-group", relationships={"tag": ["tag"]}),
        record("rir", "rir", relationships={"tag": ["tag"]}),
        record("tag", "tag"),
    ]

    positions = {item.key: index for index, item in enumerate(dependency_order(records))}

    assert positions[("tag", "tag")] < positions[("tenant_group", "tenant-group")]
    assert positions[("tenant_group", "tenant-group")] < positions[("tenant", "tenant")]
    assert positions[("tenant", "tenant")] < positions[("asn", "asn")]
    assert positions[("rir", "rir")] < positions[("asn", "asn")]
    assert positions[("site_group", "site-group")] < positions[("site", "site")]
    assert positions[("asn", "asn")] < positions[("site", "site")]


def test_dependency_order_rejects_duplicate_identity_and_cycles() -> None:
    duplicate = [record("region", "one"), record("region", "one")]
    with pytest.raises(ApplicationPlanError, match="duplicate"):
        dependency_order(duplicate)

    cycle = [
        record("region", "one", relationships={"parent": "two"}),
        record("region", "two", relationships={"parent": "one"}),
    ]
    with pytest.raises(ApplicationPlanError, match="cycle"):
        dependency_order(cycle)


def test_only_unmodeled_role_references_remain_external() -> None:
    records = [
        record("asn", "64512", attributes={"/role": "edge"}, relationships={"rir": "private"}),
        record("asn", "64513", relationships={"rir": "private"}),
    ]

    assert set(external_reference_requirements(records)) == {
        ReferenceRequirement("ipam.role", "slug", "edge"),
    }


def test_multi_value_relationships_fail_closed_on_wrong_shape() -> None:
    records = [record("site", "site-one", relationships={"tag": "managed"})]

    with pytest.raises(ApplicationPlanError, match="identity list"):
        dependency_order(records)

    assert external_reference_requirements([record("region", "region-one")]) == ()
