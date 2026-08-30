from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone
from netbox.plugins import get_plugin_config

from .models import CollectionRun, ComparisonPreparation
from .planning.service import ComparisonRejectedError, create_comparison


@dataclass(frozen=True, slots=True)
class PreparationRequest:
    preparation: ComparisonPreparation
    queued: bool


def request_comparison_preparation(
    collection_run: CollectionRun,
    *,
    force: bool = False,
) -> PreparationRequest:
    _validate_collection(collection_run)
    with transaction.atomic():
        preparation, _ = ComparisonPreparation.objects.select_for_update().get_or_create(
            collection_run=collection_run,
        )
        if preparation.state in {
            ComparisonPreparation.State.PENDING,
            ComparisonPreparation.State.RUNNING,
        } and preparation.attempt_count:
            return PreparationRequest(preparation, False)
        if (
            preparation.state == ComparisonPreparation.State.COMPLETED
            and preparation.comparison_id
            and not force
        ):
            return PreparationRequest(preparation, False)
        preparation.state = ComparisonPreparation.State.PENDING
        preparation.job_id = None
        preparation.attempt_count += 1
        preparation.started_at = None
        preparation.completed_at = None
        preparation.error = ""
        preparation.save(
            update_fields=(
                "state",
                "job_id",
                "attempt_count",
                "started_at",
                "completed_at",
                "error",
                "updated_at",
            )
        )

    try:
        from .jobs import PrepareComparisonJob

        job = PrepareComparisonJob.enqueue(
            preparation_id=str(preparation.pk),
            notifications="never",
            job_timeout=int(get_plugin_config("netbox_ssot", "comparison_job_timeout_seconds")),
        )
    except Exception as exc:
        ComparisonPreparation.objects.filter(pk=preparation.pk).update(
            state=ComparisonPreparation.State.FAILED,
            completed_at=timezone.now(),
            error=_error_message(exc),
        )
        preparation.refresh_from_db()
        return PreparationRequest(preparation, False)

    ComparisonPreparation.objects.filter(pk=preparation.pk).update(job_id=job.job_id)
    preparation.refresh_from_db()
    return PreparationRequest(preparation, True)


def prepare_comparison(preparation_id: object, *, logger: Any | None = None) -> ComparisonPreparation:
    with transaction.atomic():
        preparation = (
            ComparisonPreparation.objects.select_for_update()
            .select_related("collection_run")
            .get(pk=preparation_id)
        )
        if preparation.state != ComparisonPreparation.State.PENDING:
            return preparation
        preparation.state = ComparisonPreparation.State.RUNNING
        preparation.started_at = timezone.now()
        preparation.completed_at = None
        preparation.error = ""
        preparation.save(update_fields=("state", "started_at", "completed_at", "error", "updated_at"))

    if logger is not None:
        logger.info(
            "Preparing comparison for collection %s (%s observations)",
            preparation.collection_run_id,
            preparation.collection_run.observation_count,
        )
    try:
        outcome = create_comparison(preparation.collection_run)
    except Exception as exc:
        ComparisonPreparation.objects.filter(pk=preparation.pk).update(
            state=ComparisonPreparation.State.FAILED,
            completed_at=timezone.now(),
            error=_error_message(exc),
        )
        raise

    ComparisonPreparation.objects.filter(pk=preparation.pk).update(
        state=ComparisonPreparation.State.COMPLETED,
        comparison=outcome.comparison,
        completed_at=timezone.now(),
        error="",
    )
    preparation.refresh_from_db()
    if logger is not None:
        logger.info(
            "Comparison %s is ready: %s creates, %s updates, %s matches, %s conflicts, %s skipped",
            outcome.comparison.pk,
            outcome.comparison.create_count,
            outcome.comparison.update_count,
            outcome.comparison.no_change_count,
            outcome.comparison.conflict_count,
            outcome.comparison.skipped_count,
        )
    return preparation


def _validate_collection(collection_run: CollectionRun) -> None:
    if collection_run.state != "complete":
        raise ComparisonRejectedError("Only complete collection runs can be compared.")
    if not collection_run.completeness_token:
        raise ComparisonRejectedError("The collection run has no completeness token.")


def _error_message(exc: Exception) -> str:
    return str(exc).strip()[:2_000] or exc.__class__.__name__


__all__ = [
    "PreparationRequest",
    "prepare_comparison",
    "request_comparison_preparation",
]
