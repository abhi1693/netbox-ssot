from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Subquery

from .models import ApplyRun, ComparisonItem, ComparisonReview, ComparisonRun, ReviewDecision

ACTIONABLE_REVIEW_ACTIONS = frozenset({ComparisonItem.Action.CREATE, ComparisonItem.Action.UPDATE})


class ReviewRejectedError(ValueError):
    """The requested review transition is invalid or no longer safe."""


@dataclass(frozen=True, slots=True)
class ReviewProgress:
    actionable_count: int
    approved_count: int
    rejected_count: int
    undecided_count: int


def latest_review_decisions(
    comparison: ComparisonRun,
    *,
    item_ids: Iterable[int] | None = None,
) -> dict[int, ReviewDecision]:
    decisions = _latest_review_decision_queryset(comparison, item_ids=item_ids)
    return {decision.comparison_item_id: decision for decision in decisions}


def latest_review_decision(item: ComparisonItem) -> ReviewDecision | None:
    return (
        ReviewDecision.objects.filter(
            comparison_id=item.comparison_id,
            comparison_item_id=item.pk,
        )
        .select_related("decided_by")
        .order_by("-decided_at", "-id")
        .first()
    )


def review_progress(
    comparison: ComparisonRun,
    latest: dict[int, ReviewDecision] | None = None,
) -> ReviewProgress:
    if latest is None:
        actionable_items = comparison.items.filter(action__in=ACTIONABLE_REVIEW_ACTIONS)
        actionable_count = actionable_items.count()
        decision_counts = {
            row["decision"]: row["total"]
            for row in _latest_review_decision_queryset(comparison)
            .filter(
                comparison_item__comparison=comparison,
                comparison_item__action__in=ACTIONABLE_REVIEW_ACTIONS,
            )
            .order_by()
            .values("decision")
            .annotate(total=Count("pk"))
        }
        approved_count = decision_counts.get(ReviewDecision.Decision.APPROVE, 0)
        rejected_count = decision_counts.get(ReviewDecision.Decision.REJECT, 0)
        return ReviewProgress(
            actionable_count=actionable_count,
            approved_count=approved_count,
            rejected_count=rejected_count,
            undecided_count=actionable_count - approved_count - rejected_count,
        )

    current = latest
    actionable_ids = set(
        comparison.items.filter(action__in=ACTIONABLE_REVIEW_ACTIONS).values_list("pk", flat=True)
    )
    approved_count = sum(
        decision.decision == ReviewDecision.Decision.APPROVE
        for item_id, decision in current.items()
        if item_id in actionable_ids
    )
    rejected_count = sum(
        decision.decision == ReviewDecision.Decision.REJECT
        for item_id, decision in current.items()
        if item_id in actionable_ids
    )
    return ReviewProgress(
        actionable_count=len(actionable_ids),
        approved_count=approved_count,
        rejected_count=rejected_count,
        undecided_count=len(actionable_ids) - approved_count - rejected_count,
    )


def _latest_review_decision_queryset(
    comparison: ComparisonRun,
    *,
    item_ids: Iterable[int] | None = None,
):
    candidates = comparison.review_decisions.all()
    if item_ids is not None:
        candidates = candidates.filter(comparison_item_id__in=tuple(item_ids))
    latest_ids = (
        candidates.order_by("comparison_item_id", "-decided_at", "-id")
        .distinct("comparison_item_id")
        .values("pk")
    )
    return (
        ReviewDecision.objects.filter(comparison=comparison, pk__in=Subquery(latest_ids))
        .select_related("decided_by")
        .order_by("comparison_item_id")
    )


def record_review_decision(
    comparison: ComparisonRun,
    item: ComparisonItem,
    decision: str,
    decided_by: Any,
    *,
    reason: str = "",
) -> ReviewDecision:
    normalized_reason = reason.strip()
    if decision not in ReviewDecision.Decision.values:
        raise ReviewRejectedError("Choose approve or reject for this record.")
    if decision == ReviewDecision.Decision.REJECT and not normalized_reason:
        raise ReviewRejectedError("Explain why this record is rejected.")

    with transaction.atomic():
        locked = ComparisonRun.objects.select_for_update().get(pk=comparison.pk)
        locked_item = ComparisonItem.objects.select_for_update().get(pk=item.pk, comparison=locked)
        _ensure_open(locked)
        if locked_item.action not in ACTIONABLE_REVIEW_ACTIONS:
            raise ReviewRejectedError("Only proposed creates and updates require a review decision.")
        review_decision = ReviewDecision(
            comparison=locked,
            comparison_item=locked_item,
            decision=decision,
            decided_by=decided_by,
            reason=normalized_reason,
        )
        try:
            review_decision.full_clean()
        except ValidationError as exc:
            raise ReviewRejectedError(" ".join(exc.messages)) from exc
        review_decision.save()
        return review_decision


def approve_all_review_items(comparison: ComparisonRun, decided_by: Any) -> ReviewProgress:
    with transaction.atomic():
        locked = ComparisonRun.objects.select_for_update().get(pk=comparison.pk)
        _ensure_open(locked)
        items = list(
            ComparisonItem.objects.select_for_update()
            .filter(comparison=locked, action__in=ACTIONABLE_REVIEW_ACTIONS)
            .order_by("sequence")
        )
        latest = latest_review_decisions(locked)
        ReviewDecision.objects.bulk_create(
            [
                ReviewDecision(
                    comparison=locked,
                    comparison_item=item,
                    decision=ReviewDecision.Decision.APPROVE,
                    decided_by=decided_by,
                )
                for item in items
                if latest.get(item.pk) is None
                or latest[item.pk].decision != ReviewDecision.Decision.APPROVE
            ],
            batch_size=1_000,
        )
        return review_progress(locked)


def finalize_review(
    comparison: ComparisonRun,
    decision: str,
    reviewed_by: Any,
    *,
    reason: str = "",
) -> ComparisonReview:
    normalized_reason = reason.strip()
    if decision not in ComparisonReview.Decision.values:
        raise ReviewRejectedError("Choose whether to approve or reject this comparison.")
    if decision == ComparisonReview.Decision.REJECTED and not normalized_reason:
        raise ReviewRejectedError("Explain why this comparison is rejected.")

    with transaction.atomic():
        locked = ComparisonRun.objects.select_for_update().select_related("collection_run").get(pk=comparison.pk)
        _ensure_open(locked)
        list(ComparisonItem.objects.select_for_update().filter(comparison=locked).order_by("sequence"))
        list(ReviewDecision.objects.select_for_update().filter(comparison=locked).order_by("id"))
        latest = latest_review_decisions(locked)
        progress = review_progress(locked, latest)

        if decision == ComparisonReview.Decision.APPROVED:
            reasons: list[str] = []
            if locked.conflict_count:
                reasons.append(f"resolve all {locked.conflict_count} conflicts")
            if locked.skipped_count:
                reasons.append(f"resolve all {locked.skipped_count} skipped records")
            if progress.rejected_count:
                reasons.append(f"change {progress.rejected_count} rejected record decisions")
            if progress.undecided_count:
                reasons.append(f"review {progress.undecided_count} undecided records")
            if reasons:
                raise ReviewRejectedError("The comparison cannot be approved: " + "; ".join(reasons) + ".")

        digest = review_decision_digest(
            locked,
            latest,
            final_decision=decision,
            reviewed_by_id=str(reviewed_by.pk),
            reason=normalized_reason,
        )
        review = ComparisonReview(
            comparison=locked,
            decision=decision,
            reviewed_by=reviewed_by,
            reason=normalized_reason,
            decision_digest=digest,
            approved_count=progress.approved_count,
            rejected_count=progress.rejected_count,
        )
        try:
            review.full_clean()
        except ValidationError as exc:
            raise ReviewRejectedError(" ".join(exc.messages)) from exc
        review.save()
        return review


def review_decision_digest(
    comparison: ComparisonRun,
    latest: dict[int, ReviewDecision] | None = None,
    *,
    final_decision: str,
    reviewed_by_id: str,
    reason: str,
) -> str:
    current = latest if latest is not None else latest_review_decisions(comparison)
    items = list(comparison.items.order_by("sequence", "pk"))
    comparison_payload = {
        "id": str(comparison.pk),
        "collection_run_id": str(comparison.collection_run_id),
        "source_payload_digest": comparison.source_payload_digest,
        "target_snapshot_digest": comparison.target_snapshot_digest,
        "engine_version": comparison.engine_version,
        "counts": [
            comparison.create_count,
            comparison.update_count,
            comparison.no_change_count,
            comparison.conflict_count,
            comparison.skipped_count,
        ],
    }
    try:
        direction_is_durable = int(comparison.engine_version.partition(".")[0]) >= 6
    except ValueError:
        direction_is_durable = True
    if direction_is_durable:
        comparison_payload["direction"] = comparison.direction
    payload = {
        "comparison": comparison_payload,
        "items": [
            {
                "id": item.pk,
                "sequence": item.sequence,
                "action": item.action,
                "resource_kind": item.resource_kind,
                "identity_key": item.identity_key,
                "source_external_id": item.source_external_id,
                "target_object_type": item.target_object_type,
                "target_object_id": item.target_object_id,
                "source_data": item.source_data,
                "target_data": item.target_data,
                "changes": item.changes,
            }
            for item in items
        ],
        "decisions": [
            {
                "item_id": item_id,
                "event_id": current[item_id].pk,
                "decision": current[item_id].decision,
                "decided_by_id": str(current[item_id].decided_by_id),
                "decided_at": current[item_id].decided_at.isoformat(),
                "reason": current[item_id].reason,
            }
            for item_id in sorted(current)
        ],
        "final": {
            "decision": final_decision,
            "reviewed_by_id": reviewed_by_id,
            "reason": reason,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def review_integrity_issue(review: ComparisonReview) -> str:
    if review.decision not in ComparisonReview.Decision.values:
        return "The finalized review has an unsupported decision state."
    if review.decision == ComparisonReview.Decision.REJECTED and not review.reason.strip():
        return "The finalized rejection does not include its required reason."
    digest = review_decision_digest(
        review.comparison,
        final_decision=review.decision,
        reviewed_by_id=str(review.reviewed_by_id),
        reason=review.reason,
    )
    if digest != review.decision_digest:
        return "The finalized review no longer matches its immutable comparison decisions."
    progress = review_progress(review.comparison)
    if progress.approved_count != review.approved_count or progress.rejected_count != review.rejected_count:
        return "The finalized review summary no longer matches its immutable decisions."
    if review.decision == ComparisonReview.Decision.APPROVED and (
        progress.rejected_count or progress.undecided_count
    ):
        return "The finalized approval does not approve every actionable comparison item."
    return ""


def _ensure_open(comparison: ComparisonRun) -> None:
    if ComparisonReview.objects.filter(comparison=comparison).exists():
        raise ReviewRejectedError("This comparison already has a finalized review.")
    if ApplyRun.objects.filter(comparison=comparison).exists():
        raise ReviewRejectedError("This comparison has already been applied.")


__all__ = [
    "ACTIONABLE_REVIEW_ACTIONS",
    "ReviewProgress",
    "ReviewRejectedError",
    "approve_all_review_items",
    "finalize_review",
    "latest_review_decision",
    "latest_review_decisions",
    "record_review_decision",
    "review_decision_digest",
    "review_integrity_issue",
    "review_progress",
]
