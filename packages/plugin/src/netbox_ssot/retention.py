from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import CollectionRun, ComparisonPreparation, ComparisonRun, DiscoverySource, StoredObservation


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    source_id: UUID
    source_name: str
    generated_at: datetime
    total_runs: int
    total_observations: int
    review_protected_runs: int
    eligible_run_ids: tuple[UUID, ...]
    eligible_observations: int

    @property
    def eligible_runs(self) -> int:
        return len(self.eligible_run_ids)

    @property
    def retained_runs(self) -> int:
        return self.total_runs - self.eligible_runs


@dataclass(frozen=True, slots=True)
class RetentionResult:
    plan: RetentionPlan
    applied: bool
    deleted_runs: int = 0
    deleted_observations: int = 0


def retention_plan(source: DiscoverySource, *, now: datetime | None = None) -> RetentionPlan:
    generated_at = now or timezone.now()
    runs = CollectionRun.objects.filter(source=source)
    totals = runs.aggregate(total_runs=Count("run_id"), total_observations=Sum("observation_count"))
    newest_run_id = runs.order_by("-received_at").values_list("run_id", flat=True).first()
    successful_runs = runs.filter(state="complete").order_by("-received_at")
    newest_successful_run_id = successful_runs.values_list("run_id", flat=True).first()
    retained_successful_ids = tuple(
        successful_runs.values_list("run_id", flat=True)[: source.retention_successful_runs]
    )

    protected_ids = {run_id for run_id in (newest_run_id, newest_successful_run_id) if run_id is not None}
    unreviewed_runs = runs.filter(comparisons__isnull=True).exclude(run_id__in=protected_ids)
    successful_cutoff = generated_at - timedelta(days=source.retention_days)
    failure_cutoff = generated_at - timedelta(days=source.retention_failure_days)
    successful_candidates = unreviewed_runs.filter(state="complete").filter(
        Q(received_at__lt=successful_cutoff) | ~Q(run_id__in=retained_successful_ids)
    )
    diagnostic_candidates = unreviewed_runs.exclude(state="complete").filter(received_at__lt=failure_cutoff)
    eligible_run_ids = tuple(
        runs.filter(
            Q(run_id__in=successful_candidates.values("run_id"))
            | Q(run_id__in=diagnostic_candidates.values("run_id"))
        )
        .order_by("received_at")
        .values_list("run_id", flat=True)
    )
    eligible_observations = (
        runs.filter(run_id__in=eligible_run_ids).aggregate(total=Sum("observation_count"))["total"] or 0
    )
    return RetentionPlan(
        source_id=source.id,
        source_name=source.name,
        generated_at=generated_at,
        total_runs=totals["total_runs"] or 0,
        total_observations=totals["total_observations"] or 0,
        review_protected_runs=runs.filter(comparisons__isnull=False).distinct().count(),
        eligible_run_ids=eligible_run_ids,
        eligible_observations=eligible_observations,
    )


def prune_collections(
    source: DiscoverySource,
    *,
    apply: bool = False,
    now: datetime | None = None,
) -> RetentionResult:
    generated_at = now or timezone.now()
    if not apply:
        return RetentionResult(plan=retention_plan(source, now=generated_at), applied=False)

    with transaction.atomic():
        locked_source = DiscoverySource.objects.select_for_update().get(pk=source.pk)
        plan = retention_plan(locked_source, now=generated_at)
        if not plan.eligible_run_ids:
            return RetentionResult(plan=plan, applied=True)

        # Lock each candidate before deleting its observations. A concurrent comparison must acquire a
        # foreign-key key-share lock and therefore cannot attach reviewed evidence after this safety check.
        locked_run_ids = tuple(
            CollectionRun.objects.select_for_update()
            .filter(run_id__in=plan.eligible_run_ids)
            .exclude(run_id__in=ComparisonRun.objects.values("collection_run_id"))
            .order_by("received_at")
            .values_list("run_id", flat=True)
        )
        deleted_observations = StoredObservation.objects.filter(run_id__in=locked_run_ids).count()
        StoredObservation.objects.filter(run_id__in=locked_run_ids).delete()
        ComparisonPreparation.objects.filter(
            collection_run_id__in=locked_run_ids,
            comparison__isnull=True,
        ).delete()

        # Append-only models reject ordinary instance deletion. Retention is the sole deliberate maintenance
        # boundary and uses a locked queryset only after every protected dependency has been excluded.
        deleted_runs = CollectionRun.objects.filter(
            run_id__in=locked_run_ids,
        ).exclude(run_id__in=ComparisonRun.objects.values("collection_run_id")).count()
        CollectionRun.objects.filter(run_id__in=locked_run_ids).exclude(
            run_id__in=ComparisonRun.objects.values("collection_run_id")
        ).delete()
        return RetentionResult(
            plan=plan,
            applied=True,
            deleted_runs=deleted_runs,
            deleted_observations=deleted_observations,
        )
