from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils import timezone

from .drift import DriftSummary
from .health import SourceHealth, collection_failure_messages
from .models import (
    ApplyRun,
    CollectionRun,
    ComparisonPreparation,
    ComparisonReview,
    ComparisonRun,
    DiscoverySource,
)


@dataclass(frozen=True, slots=True)
class UIStatus:
    key: str
    label: str
    tone: str
    icon: str

    @property
    def color(self) -> str:
        """Match the color attribute exposed by the existing health status type."""
        return self.tone


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    run: CollectionRun
    comparison: ComparisonRun | None
    preparation: ComparisonPreparation | None
    review: ComparisonReview | None
    application: ApplyRun | None
    status: UIStatus
    state_group: str
    detail: str
    action_label: str
    action_url: str
    create_count: int
    update_count: int
    match_count: int
    attention_count: int

    @property
    def change_count(self) -> int:
        return self.create_count + self.update_count


@dataclass(frozen=True, slots=True)
class TrendPoint:
    day: date
    label: str
    alignment: float
    drifted: int
    attention: int
    assessed_sources: int
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class AlignmentTrend:
    points: tuple[TrendPoint, ...]
    polyline: str
    area: str
    start: date
    end: date

    @property
    def first(self) -> TrendPoint | None:
        return self.points[0] if self.points else None

    @property
    def latest(self) -> TrendPoint | None:
        return self.points[-1] if self.points else None

    @property
    def change(self) -> float:
        if len(self.points) < 2:
            return 0.0
        return self.points[-1].alignment - self.points[0].alignment


RECONCILIATION_STATES = (
    ("action_required", "Action required"),
    ("attention", "Needs attention"),
    ("review", "Needs review"),
    ("apply", "Ready to apply"),
    ("processing", "Processing"),
    ("complete", "Complete"),
)


def reconciliation_row(run: CollectionRun, *, include_comparison: bool = True) -> ReconciliationRow:
    preparation = _related(run, "comparison_preparation") if include_comparison else None
    comparisons = getattr(run, "ui_comparisons", ()) if include_comparison else ()
    comparison = comparisons[0] if comparisons else None
    if comparison is None and preparation and preparation.comparison_id:
        comparison = preparation.comparison
    review = _related(comparison, "final_review") if comparison else None
    application = _related(comparison, "apply_run") if comparison else None

    create_count = comparison.create_count if comparison else 0
    update_count = comparison.update_count if comparison else 0
    match_count = comparison.no_change_count if comparison else 0
    attention_count = comparison.conflict_count + comparison.skipped_count if comparison else 0

    if application:
        status = UIStatus("applied", "Applied", "success", "mdi-check-circle-outline")
        state_group = "complete"
        detail = f"{application.create_count + application.update_count} approved changes applied to NetBox."
        action_label = "View receipt"
        action_url = reverse("plugins:netbox_ssot:apply_detail", kwargs={"pk": application.pk})
    elif review and review.decision == ComparisonReview.Decision.REJECTED:
        status = UIStatus("rejected", "Rejected", "secondary", "mdi-close-circle-outline")
        state_group = "complete"
        detail = "The review was rejected and this reconciliation is closed."
        action_label = "View review"
        action_url = reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk})
    elif comparison and not create_count and not update_count and not attention_count:
        status = UIStatus("aligned", "Already aligned", "success", "mdi-check-decagram-outline")
        state_group = "complete"
        detail = f"All {match_count} compared records match NetBox."
        action_label = "View comparison"
        action_url = reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk})
    elif preparation and preparation.state == ComparisonPreparation.State.FAILED:
        status = UIStatus("comparison_failed", "Comparison failed", "danger", "mdi-alert-circle-outline")
        state_group = "attention"
        detail = preparation.error or "The background comparison could not be prepared."
        action_label = "Inspect collection"
        action_url = reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": run.pk})
    elif run.state != "complete":
        status = UIStatus("collection_incomplete", "Collection incomplete", "danger", "mdi-alert-outline")
        state_group = "attention"
        failure_messages = collection_failure_messages(run.messages)
        detail = " ".join(item["message"] for item in failure_messages) or (
            f"The source reported a {run.state} collection without an error message."
        )
        action_label = "Inspect evidence"
        action_url = reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": run.pk})
    elif comparison and attention_count:
        status = UIStatus("blocked", "Blocked", "danger", "mdi-alert-octagon-outline")
        state_group = "attention"
        detail = f"{attention_count} records need correction before this plan can be approved."
        action_label = "Resolve blockers"
        action_url = reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk})
    elif comparison and review and review.decision == ComparisonReview.Decision.APPROVED:
        status = UIStatus("ready_to_apply", "Ready to apply", "primary", "mdi-playlist-check")
        state_group = "apply"
        detail = f"{create_count + update_count} approved changes are waiting to be applied."
        action_label = "Apply approved plan"
        action_url = reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk}) + "#apply-actions"
    elif comparison:
        status = UIStatus("needs_review", "Needs review", "warning", "mdi-clipboard-text-search-outline")
        state_group = "review"
        detail = f"Review {create_count + update_count} proposed changes before NetBox can be updated."
        action_label = "Review changes"
        action_url = reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk})
    else:
        preparing = preparation and preparation.state in {
            ComparisonPreparation.State.PENDING,
            ComparisonPreparation.State.RUNNING,
        }
        status = UIStatus(
            "preparing" if preparing else "awaiting_comparison",
            "Preparing comparison" if preparing else "Awaiting comparison",
            "info",
            "mdi-progress-clock",
        )
        state_group = "processing"
        detail = (
            "A background worker is comparing the immutable collection with local NetBox."
            if preparing
            else "The collection is complete and ready for comparison."
        )
        action_label = "View collection"
        action_url = reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": run.pk})

    return ReconciliationRow(
        run=run,
        comparison=comparison,
        preparation=preparation,
        review=review,
        application=application,
        status=status,
        state_group=state_group,
        detail=detail,
        action_label=action_label,
        action_url=action_url,
        create_count=create_count,
        update_count=update_count,
        match_count=match_count,
        attention_count=attention_count,
    )


def build_alignment_trend(
    sources: Iterable[DiscoverySource],
    comparisons: Iterable[ComparisonRun],
    *,
    days: int,
    now: datetime | None = None,
) -> AlignmentTrend:
    current = now or timezone.now()
    end = timezone.localdate(current)
    start = end - timedelta(days=days - 1)
    source_ids = tuple(source.pk for source in sources)
    snapshots: dict[Any, list[ComparisonRun]] = defaultdict(list)
    for comparison in comparisons:
        snapshots[comparison.collection_run.source_id].append(comparison)
    for items in snapshots.values():
        items.sort(key=lambda item: item.created_at)

    raw_points: list[tuple[date, DriftSummary, int]] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        boundary = day + timedelta(days=1)
        summary = DriftSummary()
        assessed_sources = 0
        for source_id in source_ids:
            latest = None
            for comparison in snapshots.get(source_id, ()):
                if timezone.localdate(comparison.created_at) < boundary:
                    latest = comparison
                else:
                    break
            if latest is None:
                continue
            assessed_records = (
                latest.create_count
                + latest.update_count
                + latest.no_change_count
                + latest.conflict_count
                + latest.skipped_count
            )
            if not assessed_records:
                continue
            assessed_sources += 1
            summary += DriftSummary(
                missing_locally=latest.create_count,
                different_locally=latest.update_count,
                matching=latest.no_change_count,
                needs_attention=latest.conflict_count + latest.skipped_count,
            )
        if assessed_sources:
            raw_points.append((day, summary, assessed_sources))

    point_count = max(1, len(raw_points) - 1)
    points = tuple(
        TrendPoint(
            day=day,
            label=day.strftime("%b %-d"),
            alignment=summary.alignment_percentage,
            drifted=summary.drifted,
            attention=summary.needs_attention,
            assessed_sources=assessed_sources,
            x=50 if len(raw_points) == 1 else 2 + index / point_count * 96,
            y=92 - summary.alignment_percentage * 0.84,
        )
        for index, (day, summary, assessed_sources) in enumerate(raw_points)
    )
    polyline = " ".join(f"{point.x:.2f},{point.y:.2f}" for point in points)
    area = f"2,92 {polyline} 98,92" if len(points) > 1 else ""
    return AlignmentTrend(
        points=points,
        polyline=polyline,
        area=area,
        start=points[0].day if points else start,
        end=end,
    )


def source_attention_label(health: SourceHealth) -> str:
    return health.status.label


def _related(instance: Any, name: str) -> Any | None:
    if instance is None:
        return None
    try:
        return getattr(instance, name)
    except (AttributeError, ObjectDoesNotExist):
        return None


__all__ = [
    "RECONCILIATION_STATES",
    "AlignmentTrend",
    "ReconciliationRow",
    "TrendPoint",
    "UIStatus",
    "build_alignment_trend",
    "reconciliation_row",
    "source_attention_label",
]
