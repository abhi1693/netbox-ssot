from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from netbox_ssot.providers import ProviderNotFoundError, ProviderRegistry
from netbox_ssot_contracts import (
    ChangeAction,
    ChangeProposal,
    CollectionState,
    DecisionKind,
    Evidence,
    FieldChange,
    MatchEvidence,
    MatchKind,
    Observation,
    ObservationBatch,
    PlanDecision,
    ProviderManifest,
    ReconciliationPlan,
    ResourceKind,
    SchemaContractError,
    assert_provider_contract,
    normalize_config_schema,
    selected_dataset_ids,
    validate_config_schema,
)
from netbox_ssot_provider_netbox import provider_definition

MANIFEST = provider_definition().manifest
NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def manifest_with(**updates: Any) -> dict[str, Any]:
    payload = MANIFEST.model_dump(mode="python")
    payload.update(updates)
    return payload


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"execution_modes": ("agent", "agent")}, "execution_modes must be unique"),
        ({"capabilities": ("source_read", "source_read")}, "capabilities must be unique"),
        ({"icon_class": "<script>"}, "String should match pattern"),
        (
            {
                "agent_compatibility": {
                    "protocol_version": "1.0",
                    "minimum_agent_version": "0.1.0",
                    "collector_id": "unifi",
                }
            },
            "collector ID must equal",
        ),
        ({"datasets": (MANIFEST.datasets[0], MANIFEST.datasets[0])}, "dataset IDs must be unique"),
    ],
)
def test_manifest_rejects_ambiguous_identity(updates: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ProviderManifest.model_validate(manifest_with(**updates))


def test_manifest_rejects_unknown_and_self_dataset_dependencies() -> None:
    datasets = [dataset.model_dump(mode="python") for dataset in MANIFEST.datasets]
    datasets[0]["depends_on"] = ("missing",)
    with pytest.raises(ValidationError, match="unknown datasets"):
        ProviderManifest.model_validate(manifest_with(datasets=datasets))

    datasets[0]["depends_on"] = (datasets[0]["id"],)
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        ProviderManifest.model_validate(manifest_with(datasets=datasets))


def test_dataset_data_mappings_are_unique_and_cover_declared_resource_kinds() -> None:
    datasets = [dataset.model_dump(mode="python") for dataset in MANIFEST.datasets]
    region_mapping = datasets[1]["data_mappings"][0]
    datasets[1]["data_mappings"] = (region_mapping, region_mapping)
    with pytest.raises(ValidationError, match="data mappings must be unique"):
        ProviderManifest.model_validate(manifest_with(datasets=datasets))

    datasets = [dataset.model_dump(mode="python") for dataset in MANIFEST.datasets]
    datasets[1]["data_mappings"][0]["destination_kind"] = "site"
    with pytest.raises(ValidationError, match="must cover every resource kind"):
        ProviderManifest.model_validate(manifest_with(datasets=datasets))

    datasets = [dataset.model_dump(mode="python") for dataset in MANIFEST.datasets]
    datasets[1]["data_mappings"][0]["source_path"] = "https://unsafe.example.com/dcim/regions/"
    with pytest.raises(ValidationError, match="String should match pattern"):
        ProviderManifest.model_validate(manifest_with(datasets=datasets))


def test_manifest_instance_url_field_must_reference_a_uri_property() -> None:
    with pytest.raises(ValidationError, match="must identify a configuration property"):
        ProviderManifest.model_validate(manifest_with(instance_url_field="missing"))
    with pytest.raises(ValidationError, match="must identify a string URI property"):
        ProviderManifest.model_validate(manifest_with(instance_url_field="page_size"))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda schema: schema.update({"unknown": True}), "unsupported root schema keys"),
        (lambda schema: schema.update({"type": "array"}), "root must have type"),
        (lambda schema: schema.update({"additionalProperties": True}), "additionalProperties to false"),
        (lambda schema: schema.update({"properties": []}), "invalid JSON Schema"),
        (lambda schema: schema.update({"required": "base_url"}), "invalid JSON Schema"),
        (lambda schema: schema.update({"required": ["missing"]}), "required contains unknown"),
        (lambda schema: schema.update({"x-netbox-ssot-order": ["base_url"]}), "must list every property"),
    ],
)
def test_schema_root_guards(mutation: Any, message: str) -> None:
    schema = deepcopy(MANIFEST.config_schema)
    mutation(schema)
    with pytest.raises(SchemaContractError, match=message):
        validate_config_schema(schema)


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ([], "invalid JSON Schema"),
        ({"type": "object", "title": "Nested"}, "unsupported type"),
        ({"type": "string"}, "must have a title"),
        ({"type": "array", "title": "Array", "items": []}, "invalid JSON Schema"),
        ({"type": "array", "title": "Array", "items": {"type": "integer", "enum": [1]}}, "string enum items"),
        ({"type": "string", "title": "Text", "items": {"type": "string"}}, "cannot declare items"),
        ({"type": "string", "title": "Text", "x-netbox-ssot-widget": "html"}, "unsupported widget"),
        (
            {"type": "string", "title": "Text", "x-netbox-ssot-widget": "secret-reference"},
            "without being marked secret",
        ),
        ({"type": "integer", "title": "Secret", "writeOnly": True}, "must be a string reference"),
    ],
)
def test_schema_property_guards(definition: Any, message: str) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"field": definition},
        "x-netbox-ssot-order": ["field"],
    }
    with pytest.raises(SchemaContractError, match=message):
        validate_config_schema(schema)


def test_invalid_json_schema_is_reported_as_contract_error() -> None:
    with pytest.raises(SchemaContractError, match="invalid JSON Schema"):
        validate_config_schema({"type": 42})


def test_schema_normalization_covers_text_select_array_and_pointer_escaping() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "plain": {"type": "string", "title": "Plain"},
            "mode": {"type": "string", "title": "Mode", "enum": ["a", "b"]},
            "sites": {"type": "array", "title": "Sites", "items": {"type": "string", "enum": ["home"]}},
            "a/b~c": {"type": "string", "title": "Escaped"},
        },
        "x-netbox-ssot-order": ["plain", "mode", "sites", "a/b~c"],
    }
    fields = normalize_config_schema(schema)
    assert tuple(field.widget for field in fields) == ("text", "select", "multiselect", "text")
    assert fields[-1].pointer == "/a~1b~0c"


def observation(*, source_id: Any | None = None, provider_id: str = "netbox") -> Observation:
    source_id = source_id or uuid4()
    return Observation.from_mapping(
        resource_kind=ResourceKind.SITE,
        external_id="netbox:site:1",
        source_id=source_id,
        provider_id=provider_id,
        scope=(),
        collected_at=NOW,
        attributes={"/name": "Home"},
        evidence=(Evidence(source_object_type="site", source_object_id="1", observed_at=NOW),),
    )


def test_observation_and_evidence_require_timezone_and_unique_scope() -> None:
    payload = observation().model_dump(mode="python")
    payload["collected_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="collected_at must include"):
        Observation.model_validate(payload)

    payload = observation().model_dump(mode="python")
    payload["scope"] = ({"name": "site", "value": "one"}, {"name": "site", "value": "two"})
    with pytest.raises(ValidationError, match="scope dimension names must be unique"):
        Observation.model_validate(payload)

    with pytest.raises(ValidationError, match="timestamps must include"):
        Evidence(source_object_type="site", source_object_id="1", observed_at=NOW.replace(tzinfo=None))


def batch_payload(item: Observation) -> dict[str, Any]:
    return {
        "run_id": uuid4(),
        "source_id": item.source_id,
        "provider_id": item.provider_id,
        "provider_version": "0.1.0",
        "contract_version": "1.0",
        "state": CollectionState.PARTIAL,
        "started_at": NOW,
        "completed_at": NOW,
        "datasets": ("sites",),
        "scope": (),
        "observations": (item,),
    }


def test_batch_rejects_bad_time_and_cross_source_observations() -> None:
    item = observation()
    payload = batch_payload(item)
    payload["started_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="batch timestamps"):
        ObservationBatch.model_validate(payload)

    payload = batch_payload(item)
    payload["completed_at"] = NOW - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="cannot precede"):
        ObservationBatch.model_validate(payload)

    payload = batch_payload(item)
    payload["source_id"] = uuid4()
    with pytest.raises(ValidationError, match="batch source"):
        ObservationBatch.model_validate(payload)

    payload = batch_payload(item)
    payload["provider_id"] = "unifi"
    with pytest.raises(ValidationError, match="batch provider"):
        ObservationBatch.model_validate(payload)


def planning_evidence() -> tuple[Evidence, ...]:
    return (Evidence(source_object_type="site", source_object_id="1", observed_at=NOW),)


def test_planning_contract_rejects_ambiguous_or_empty_changes() -> None:
    with pytest.raises(ValidationError, match="authoritative confidence"):
        MatchEvidence(kind=MatchKind.FUZZY_SUGGESTION, confidence=1, explanation="similar")
    with pytest.raises(ValidationError, match="must differ"):
        FieldChange(path="/name", before="same", after="same", owner="netbox")

    create = {
        "change_id": uuid4(),
        "action": ChangeAction.CREATE,
        "resource_kind": ResourceKind.SITE,
        "external_id": "netbox:site:1",
        "evidence": planning_evidence(),
    }
    with pytest.raises(ValidationError, match="cannot identify"):
        ChangeProposal(**create, target_object_id="1")

    update = {**create, "action": ChangeAction.UPDATE, "target_object_id": "1"}
    with pytest.raises(ValidationError, match="at least one field"):
        ChangeProposal(**update)
    with pytest.raises(ValidationError, match="require a target"):
        ChangeProposal(**{**update, "target_object_id": None})

    delete = {**create, "action": ChangeAction.DELETE, "target_object_id": "1"}
    with pytest.raises(ValidationError, match="explicitly marked destructive"):
        ChangeProposal(**delete)

    with pytest.raises(ValidationError, match="cannot produce automatic"):
        ChangeProposal(
            **create,
            automatic=True,
            match=MatchEvidence(kind=MatchKind.FUZZY_SUGGESTION, confidence=0.8, explanation="similar"),
        )


def valid_create(**updates: Any) -> ChangeProposal:
    payload: dict[str, Any] = {
        "change_id": uuid4(),
        "action": ChangeAction.CREATE,
        "resource_kind": ResourceKind.SITE,
        "external_id": "netbox:site:1",
        "evidence": planning_evidence(),
    }
    payload.update(updates)
    return ChangeProposal(**payload)


def plan_payload(changes: tuple[ChangeProposal, ...]) -> dict[str, Any]:
    return {
        "plan_id": uuid4(),
        "source_id": uuid4(),
        "run_ids": (uuid4(),),
        "created_at": NOW,
        "target_snapshot_fingerprint": "0" * 64,
        "changes": changes,
    }


def test_plan_rejects_naive_time_duplicate_and_invalid_dependencies() -> None:
    change = valid_create()
    payload = plan_payload((change,))
    payload["created_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="created_at must include"):
        ReconciliationPlan.model_validate(payload)

    with pytest.raises(ValidationError, match="change IDs must be unique"):
        ReconciliationPlan.model_validate(plan_payload((change, change)))

    unknown = valid_create(depends_on=(uuid4(),))
    with pytest.raises(ValidationError, match="unknown dependencies"):
        ReconciliationPlan.model_validate(plan_payload((unknown,)))

    change_id = uuid4()
    recursive = valid_create(change_id=change_id, depends_on=(change_id,))
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        ReconciliationPlan.model_validate(plan_payload((recursive,)))


def test_plan_decision_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="decided_at must include"):
        PlanDecision(
            plan_id=uuid4(),
            change_id=uuid4(),
            decision=DecisionKind.APPROVE,
            decided_by="reviewer",
            decided_at=NOW.replace(tzinfo=None),
        )


@dataclass
class EntryPoint:
    name: str
    value: str
    loaded: Any

    def load(self) -> Any:
        return self.loaded


class Definition:
    def __init__(self, manifest: ProviderManifest) -> None:
        self.manifest = manifest


def test_registry_is_cached_and_get_reports_missing_provider() -> None:
    calls = 0

    def load() -> tuple[EntryPoint, ...]:
        nonlocal calls
        calls += 1
        return (EntryPoint("netbox", "test:provider", lambda: Definition(MANIFEST)),)

    registry = ProviderRegistry(entry_point_loader=load)
    assert registry.discover() is registry.discover()
    assert calls == 1
    registry.discover(refresh=True)
    assert calls == 2
    with pytest.raises(ProviderNotFoundError):
        registry.get("missing")


def test_registry_isolates_invalid_provider_shapes_and_identities() -> None:
    incompatible = MANIFEST.model_copy(update={"contract_version": "2.0"})
    entries = (
        EntryPoint("noncallable", "test:noncallable", object()),
        EntryPoint("invalid", "test:invalid", lambda: object()),
        EntryPoint("netbox", "test:incompatible", lambda: Definition(incompatible)),
        EntryPoint("wrong", "test:wrong", lambda: Definition(MANIFEST)),
        EntryPoint("netbox", "test:first", lambda: Definition(MANIFEST)),
        EntryPoint("netbox", "test:duplicate", lambda: Definition(MANIFEST)),
    )
    catalog = ProviderRegistry(entry_point_loader=lambda: entries).discover()
    assert tuple(item.manifest.provider_id for item in catalog.providers) == ("netbox",)
    assert len(catalog.failures) == 5


def test_provider_helpers_reject_invalid_provider_and_unknown_dataset() -> None:
    with pytest.raises(TypeError, match="ProviderDefinition"):
        assert_provider_contract(object())
    with pytest.raises(ValueError, match="unknown datasets"):
        selected_dataset_ids(MANIFEST, ("missing",))
