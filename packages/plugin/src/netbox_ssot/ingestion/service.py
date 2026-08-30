from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone
from netbox.plugins import get_plugin_config

from netbox_ssot_contracts import ObservationBatch, selected_dataset_ids

from ..models import CollectionRun, CollectorAgent, DiscoverySource, StoredObservation
from ..providers import ProviderNotFoundError, ProviderRegistry


class IngestionRejectedError(ValueError):
    """The signed batch is not authorized for the claimed source or provider."""


class IngestionConflictError(ValueError):
    """The run ID was already used for different content or by a different agent."""


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    status: str
    run_id: str
    observation_count: int
    payload_digest: str


def canonical_batch_digest(batch: ObservationBatch) -> str:
    encoded = json.dumps(batch.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_provider(batch: ObservationBatch) -> None:
    try:
        manifest = ProviderRegistry().get(batch.provider_id).manifest
    except ProviderNotFoundError as exc:
        raise IngestionRejectedError("Batch provider is not installed.") from exc
    if batch.contract_version != manifest.contract_version:
        raise IngestionRejectedError("Batch contract version is incompatible with the installed provider.")
    try:
        resolved = selected_dataset_ids(manifest, batch.datasets)
    except ValueError as exc:
        raise IngestionRejectedError("Batch contains an unknown provider dataset.") from exc
    if resolved != batch.datasets:
        raise IngestionRejectedError("Batch does not include its declared dataset dependencies.")
    allowed_kinds = {
        kind for dataset in manifest.datasets if dataset.id in batch.datasets for kind in dataset.resource_kinds
    }
    if any(observation.resource_kind not in allowed_kinds for observation in batch.observations):
        raise IngestionRejectedError("Batch contains a resource kind outside its declared datasets.")


@transaction.atomic
def ingest_batch(*, agent: CollectorAgent, batch: ObservationBatch) -> IngestionOutcome:
    maximum = int(get_plugin_config("netbox_ssot", "maximum_observations_per_batch"))
    if len(batch.observations) > maximum:
        raise IngestionRejectedError("Batch exceeds the configured observation limit.")
    _validate_provider(batch)

    try:
        source = DiscoverySource.objects.select_for_update().get(pk=batch.source_id, enabled=True)
    except DiscoverySource.DoesNotExist as exc:
        raise IngestionRejectedError("Batch source is not registered or enabled.") from exc
    if source.provider_id != batch.provider_id:
        raise IngestionRejectedError("Batch provider does not match the registered source.")
    if not agent.enabled or source.assigned_agent_id != agent.pk:
        raise IngestionRejectedError("Agent is not authorized for the batch source.")

    digest = canonical_batch_digest(batch)
    existing = CollectionRun.objects.filter(pk=batch.run_id).first()
    if existing is not None:
        if existing.payload_digest != digest or existing.agent_id != agent.pk or existing.source_id != source.pk:
            raise IngestionConflictError("Run ID is already associated with a different batch.")
        DiscoverySource.objects.filter(pk=source.pk).update(
            active_collection_started_at=None,
            active_collection_seen_at=None,
        )
        return IngestionOutcome("duplicate", str(existing.run_id), existing.observation_count, digest)

    run = CollectionRun.objects.create(
        run_id=batch.run_id,
        source=source,
        agent=agent,
        provider_id=batch.provider_id,
        provider_version=batch.provider_version,
        contract_version=batch.contract_version,
        state=batch.state,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        datasets=list(batch.datasets),
        scope=[dimension.model_dump(mode="json") for dimension in batch.scope],
        messages=[message.model_dump(mode="json") for message in batch.messages],
        completeness_token=batch.completeness_token or "",
        payload_digest=digest,
        observation_count=len(batch.observations),
    )
    StoredObservation.objects.bulk_create(
        [
            StoredObservation(
                run=run,
                source=source,
                sequence=sequence,
                resource_kind=observation.resource_kind,
                external_id=observation.external_id,
                collected_at=observation.collected_at,
                scope=[dimension.model_dump(mode="json") for dimension in observation.scope],
                attributes=[attribute.model_dump(mode="json") for attribute in observation.attributes],
                relationships=[relationship.model_dump(mode="json") for relationship in observation.relationships],
                evidence=[item.model_dump(mode="json") for item in observation.evidence],
                fingerprint=observation.fingerprint,
            )
            for sequence, observation in enumerate(batch.observations)
        ],
        batch_size=1_000,
    )
    CollectorAgent.objects.filter(pk=agent.pk).update(last_seen_at=timezone.now())
    DiscoverySource.objects.filter(pk=source.pk).update(
        active_collection_started_at=None,
        active_collection_seen_at=None,
    )
    return IngestionOutcome("accepted", str(run.run_id), run.observation_count, digest)
