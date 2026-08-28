from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from .base import AttributeValue, ContractModel, Identifier, JsonPointer, TimestampedContractModel
from .manifest import ResourceKind


class CollectionState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class ObservationAttribute(ContractModel):
    path: JsonPointer
    value: AttributeValue


class ScopeDimension(ContractModel):
    name: Identifier
    value: str = Field(min_length=1, max_length=256)


class Relationship(ContractModel):
    kind: Identifier
    target_kind: ResourceKind
    target_external_id: str = Field(min_length=1, max_length=512)


class Evidence(TimestampedContractModel):
    source_object_type: str = Field(min_length=1, max_length=128)
    source_object_id: str = Field(min_length=1, max_length=512)
    attribute_paths: tuple[JsonPointer, ...] = ()
    raw_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    note: str = Field(default="", max_length=500)


class Observation(ContractModel):
    resource_kind: ResourceKind
    external_id: str = Field(min_length=1, max_length=512)
    source_id: UUID
    provider_id: Identifier
    scope: tuple[ScopeDimension, ...]
    collected_at: datetime
    attributes: tuple[ObservationAttribute, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    evidence: tuple[Evidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_observation(self) -> Observation:
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("collected_at must include a timezone")
        paths = [attribute.path for attribute in self.attributes]
        if len(set(paths)) != len(paths):
            raise ValueError("attribute paths must be unique")
        dimensions = [dimension.name for dimension in self.scope]
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("scope dimension names must be unique")
        return self

    @classmethod
    def from_mapping(
        cls,
        *,
        resource_kind: ResourceKind,
        external_id: str,
        source_id: UUID,
        provider_id: Identifier,
        scope: tuple[ScopeDimension, ...],
        collected_at: datetime,
        attributes: dict[str, AttributeValue],
        relationships: tuple[Relationship, ...] = (),
        evidence: tuple[Evidence, ...],
    ) -> Self:
        return cls(
            resource_kind=resource_kind,
            external_id=external_id,
            source_id=source_id,
            provider_id=provider_id,
            scope=scope,
            collected_at=collected_at,
            attributes=tuple(
                ObservationAttribute(path=path, value=value) for path, value in sorted(attributes.items())
            ),
            relationships=relationships,
            evidence=evidence,
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "resource_kind": self.resource_kind,
            "external_id": self.external_id,
            "source_id": str(self.source_id),
            "provider_id": self.provider_id,
            "scope": [dimension.model_dump(mode="json") for dimension in self.scope],
            "attributes": [attribute.model_dump(mode="json") for attribute in self.attributes],
            "relationships": [relationship.model_dump(mode="json") for relationship in self.relationships],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class CollectionMessage(ContractModel):
    code: Identifier
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False


class ObservationBatch(ContractModel):
    run_id: UUID
    source_id: UUID
    provider_id: Identifier
    provider_version: str
    contract_version: str
    state: CollectionState
    started_at: datetime
    completed_at: datetime
    datasets: tuple[Identifier, ...] = Field(min_length=1)
    scope: tuple[ScopeDimension, ...]
    observations: tuple[Observation, ...] = ()
    messages: tuple[CollectionMessage, ...] = ()
    completeness_token: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_batch(self) -> ObservationBatch:
        for timestamp in (self.started_at, self.completed_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("batch timestamps must include a timezone")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.state is CollectionState.COMPLETE and not self.completeness_token:
            raise ValueError("complete collections require a completeness_token")
        if self.state is CollectionState.FAILED and self.observations:
            raise ValueError("failed collections cannot contain observations")
        if len(set(self.datasets)) != len(self.datasets):
            raise ValueError("batch datasets must be unique")
        identities = [(observation.resource_kind, observation.external_id) for observation in self.observations]
        if len(set(identities)) != len(identities):
            raise ValueError("batch observation identities must be unique")
        if any(observation.source_id != self.source_id for observation in self.observations):
            raise ValueError("all observations must belong to the batch source")
        if any(observation.provider_id != self.provider_id for observation in self.observations):
            raise ValueError("all observations must belong to the batch provider")
        return self
