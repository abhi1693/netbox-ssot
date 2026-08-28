from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netbox_ssot_contracts import (
    CollectionState,
    Evidence,
    Observation,
    ObservationBatch,
    ResourceKind,
    ScopeDimension,
)


def evidence(observed_at: datetime) -> tuple[Evidence, ...]:
    return (
        Evidence(
            source_object_type="device",
            source_object_id="abc123",
            attribute_paths=("/name", "/serial"),
            observed_at=observed_at,
        ),
    )


def test_observation_fingerprint_tracks_facts_not_collection_time() -> None:
    source_id = uuid4()
    first_time = datetime(2026, 8, 28, 12, tzinfo=UTC)
    second_time = first_time + timedelta(hours=1)

    first = Observation.from_mapping(
        resource_kind=ResourceKind.DEVICE,
        external_id="device:abc123",
        source_id=source_id,
        provider_id="netbox",
        scope=(ScopeDimension(name="site", value="home"),),
        collected_at=first_time,
        attributes={"/serial": "ABC123", "/name": "switch-1"},
        evidence=evidence(first_time),
    )
    second = Observation.from_mapping(
        resource_kind=ResourceKind.DEVICE,
        external_id="device:abc123",
        source_id=source_id,
        provider_id="netbox",
        scope=(ScopeDimension(name="site", value="home"),),
        collected_at=second_time,
        attributes={"/name": "switch-1", "/serial": "ABC123"},
        evidence=evidence(second_time),
    )

    assert first.fingerprint == second.fingerprint
    with pytest.raises(ValidationError, match="Instance is frozen"):
        first.external_id = "changed"  # type: ignore[misc]


def test_duplicate_attribute_paths_are_rejected() -> None:
    data = Observation.from_mapping(
        resource_kind=ResourceKind.DEVICE,
        external_id="device:abc123",
        source_id=uuid4(),
        provider_id="netbox",
        scope=(),
        collected_at=datetime.now(UTC),
        attributes={"/name": "switch-1"},
        evidence=evidence(datetime.now(UTC)),
    ).model_dump(mode="python")
    data["attributes"] = (
        {"path": "/name", "value": "switch-1"},
        {"path": "/name", "value": "switch-2"},
    )

    with pytest.raises(ValidationError, match="attribute paths must be unique"):
        Observation.model_validate(data)


def test_complete_batch_requires_scope_completeness_token() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError, match="completeness_token"):
        ObservationBatch(
            run_id=uuid4(),
            source_id=uuid4(),
            provider_id="netbox",
            provider_version="0.1.0",
            contract_version="1.0",
            state=CollectionState.COMPLETE,
            started_at=now,
            completed_at=now,
            datasets=("sites",),
            scope=(),
        )


def test_failed_batch_cannot_smuggle_partial_observations() -> None:
    now = datetime.now(UTC)
    source_id = uuid4()
    observation = Observation.from_mapping(
        resource_kind=ResourceKind.SITE,
        external_id="site:home",
        source_id=source_id,
        provider_id="netbox",
        scope=(),
        collected_at=now,
        attributes={"/name": "Home"},
        evidence=evidence(now),
    )

    with pytest.raises(ValidationError, match="failed collections cannot contain observations"):
        ObservationBatch(
            run_id=uuid4(),
            source_id=source_id,
            provider_id="netbox",
            provider_version="0.1.0",
            contract_version="1.0",
            state=CollectionState.FAILED,
            started_at=now,
            completed_at=now,
            datasets=("sites",),
            scope=(),
            observations=(observation,),
        )
