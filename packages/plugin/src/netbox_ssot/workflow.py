from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest
from django.urls import reverse

from .application.service import ApplicationReadiness
from .models import ApplyRun, CollectionRun, ComparisonReview, ComparisonRun

APPROVAL_REQUIRED_REASON = "This comparison must be approved in a finalized review before it can be applied."


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    number: int
    label: str
    state: str
    detail: str
    url: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowAction:
    label: str
    url: str
    method: str
    allowed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationWorkflow:
    steps: tuple[WorkflowStep, ...]
    action: WorkflowAction | None


def workflow_presentation(
    request: HttpRequest,
    run: CollectionRun,
    *,
    comparison: ComparisonRun | None = None,
    final_review: ComparisonReview | None = None,
    application: ApplyRun | None = None,
    readiness: ApplicationReadiness | None = None,
    progress: Any | None = None,
    current_stage: str = "",
) -> ReconciliationWorkflow:
    run_url = reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": run.pk})
    comparison_url = (
        reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": comparison.pk})
        if comparison is not None
        else ""
    )
    application_url = (
        reverse("plugins:netbox_ssot:apply_detail", kwargs={"pk": application.pk})
        if application is not None
        else ""
    )
    compare_url = reverse("plugins:netbox_ssot:comparison_add", kwargs={"pk": run.pk})
    run_now_url = reverse("plugins:netbox_ssot:source_run_now", kwargs={"pk": run.source_id})
    source_url = reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": run.source_id})

    collection_complete = run.state == "complete" and bool(run.completeness_token)
    has_changes = bool(comparison and (comparison.create_count or comparison.update_count))
    blocked_records = bool(comparison and (comparison.conflict_count or comparison.skipped_count))
    no_changes = bool(comparison and not has_changes and not blocked_records)
    stale = bool(
        comparison
        and readiness is not None
        and readiness.current_target_digest != comparison.target_snapshot_digest
    )
    readiness_blockers = (
        tuple(reason for reason in readiness.reasons if reason != APPROVAL_REQUIRED_REASON)
        if readiness is not None
        else ()
    )
    preapproval_blocked = bool(final_review is None and readiness_blockers)
    review_rejected = bool(final_review and final_review.decision == ComparisonReview.Decision.REJECTED)
    review_approved = bool(final_review and final_review.decision == ComparisonReview.Decision.APPROVED)

    collection_step = WorkflowStep(
        1,
        "Collect",
        "complete" if collection_complete else "blocked",
        (
            f"{run.observation_count} immutable observations received."
            if collection_complete
            else f"Collection {run.state}; comparison is unavailable."
        ),
        run_url,
    )
    compare_step = _compare_step(collection_complete, comparison, stale, comparison_url)
    review_step = _review_step(
        comparison,
        final_review,
        progress,
        comparison_url,
        stale=stale,
        no_changes=no_changes,
        blocked=blocked_records or preapproval_blocked,
        rejected=review_rejected,
        approved=review_approved,
    )
    apply_step = _apply_step(
        application,
        readiness,
        comparison_url,
        application_url,
        no_changes=no_changes,
        blocked=stale or blocked_records or preapproval_blocked or review_rejected,
        approved=review_approved,
    )
    action = _next_action(
        request,
        comparison,
        application,
        readiness,
        final_review,
        compare_url=compare_url,
        comparison_url=comparison_url,
        application_url=application_url,
        run_now_url=run_now_url,
        source_url=source_url,
        current_stage=current_stage,
        collection_complete=collection_complete,
        stale=stale,
        no_changes=no_changes,
        blocked=blocked_records or preapproval_blocked,
        rejected=review_rejected,
        approved=review_approved,
    )
    return ReconciliationWorkflow(
        steps=(collection_step, compare_step, review_step, apply_step),
        action=action,
    )


def _compare_step(
    collection_complete: bool,
    comparison: ComparisonRun | None,
    stale: bool,
    comparison_url: str,
) -> WorkflowStep:
    if comparison is None:
        return WorkflowStep(
            2,
            "Compare",
            "current" if collection_complete else "pending",
            "Compare the collection with current local NetBox data.",
        )
    if stale:
        return WorkflowStep(
            2,
            "Compare",
            "blocked",
            "Local NetBox changed after this snapshot; compare again.",
            comparison_url,
        )
    return WorkflowStep(
        2,
        "Compare",
        "complete",
        (
            f"{comparison.create_count} creates, {comparison.update_count} updates, "
            f"{comparison.no_change_count} matches."
        ),
        comparison_url,
    )


def _review_step(
    comparison: ComparisonRun | None,
    final_review: ComparisonReview | None,
    progress: Any | None,
    comparison_url: str,
    *,
    stale: bool,
    no_changes: bool,
    blocked: bool,
    rejected: bool,
    approved: bool,
) -> WorkflowStep:
    if comparison is None or stale:
        return WorkflowStep(3, "Review", "pending", "A current comparison is required first.")
    if no_changes:
        return WorkflowStep(
            3,
            "Review",
            "complete",
            "No decisions are required because everything matches.",
            comparison_url,
        )
    if blocked:
        return WorkflowStep(
            3,
            "Review",
            "blocked",
            "Resolve the displayed blockers, then create a fresh comparison.",
            comparison_url,
        )
    if rejected:
        return WorkflowStep(3, "Review", "blocked", "The review was rejected and is immutable.", comparison_url)
    if approved:
        return WorkflowStep(3, "Review", "complete", f"Approved by {final_review.reviewed_by}.", comparison_url)
    remaining = (
        progress.undecided_count
        if progress is not None
        else comparison.create_count + comparison.update_count
    )
    return WorkflowStep(
        3,
        "Review",
        "current",
        f"Review {remaining} undecided proposed change{'' if remaining == 1 else 's'}.",
        comparison_url + "#review-actions",
    )


def _apply_step(
    application: ApplyRun | None,
    readiness: ApplicationReadiness | None,
    comparison_url: str,
    application_url: str,
    *,
    no_changes: bool,
    blocked: bool,
    approved: bool,
) -> WorkflowStep:
    if application is not None:
        return WorkflowStep(4, "Apply", "complete", f"Applied by {application.applied_by}.", application_url)
    if no_changes:
        return WorkflowStep(
            4,
            "Apply",
            "complete",
            "Not needed; source and destination already match.",
            comparison_url,
        )
    if blocked:
        return WorkflowStep(
            4,
            "Apply",
            "blocked",
            "Unavailable until a fresh, applicable comparison is approved.",
        )
    if approved:
        return WorkflowStep(
            4,
            "Apply",
            "current" if readiness is None or readiness.ready else "blocked",
            (
                "Apply the approved plan atomically."
                if readiness is None or readiness.ready
                else "Resolve the apply requirements shown below."
            ),
            comparison_url + "#apply-actions",
        )
    return WorkflowStep(4, "Apply", "pending", "Approval is required before NetBox can be changed.")


def _next_action(
    request: HttpRequest,
    comparison: ComparisonRun | None,
    application: ApplyRun | None,
    readiness: ApplicationReadiness | None,
    final_review: ComparisonReview | None,
    *,
    compare_url: str,
    comparison_url: str,
    application_url: str,
    run_now_url: str,
    source_url: str,
    current_stage: str,
    collection_complete: bool,
    stale: bool,
    no_changes: bool,
    blocked: bool,
    rejected: bool,
    approved: bool,
) -> WorkflowAction | None:
    if collection_complete and comparison is None:
        return WorkflowAction(
            "Compare with NetBox",
            compare_url,
            "post",
            request.user.has_perm("netbox_ssot.add_comparisonrun"),
            "This creates a read-only comparison; it does not change NetBox.",
        )
    if application is not None:
        return WorkflowAction(
            "Back to source" if current_stage == "apply" else "View applied result",
            source_url if current_stage == "apply" else application_url,
            "get",
            request.user.has_perm(
                "netbox_ssot.view_discoverysource" if current_stage == "apply" else "netbox_ssot.view_applyrun"
            ),
            "The workflow completed successfully.",
        )
    if stale:
        return WorkflowAction(
            "Compare again",
            compare_url,
            "post",
            request.user.has_perm("netbox_ssot.add_comparisonrun"),
            "Create a new comparison against current local NetBox data.",
        )
    if no_changes:
        return WorkflowAction(
            "Back to source" if current_stage == "comparison" else "View matching records",
            source_url if current_stage == "comparison" else comparison_url,
            "get",
            request.user.has_perm(
                "netbox_ssot.view_discoverysource"
                if current_stage == "comparison"
                else "netbox_ssot.view_comparisonrun"
            ),
            "The workflow is complete; no apply step is needed.",
        )
    if blocked:
        blocked_action = "skipped" if comparison and comparison.skipped_count else "conflict"
        return WorkflowAction(
            "Review blockers",
            comparison_url + f"?action={blocked_action}",
            "get",
            request.user.has_perm("netbox_ssot.view_comparisonrun"),
            "Fix the source or local prerequisite, then compare again.",
        )
    if rejected:
        return WorkflowAction(
            "Run a new collection",
            run_now_url,
            "post",
            request.user.has_perm("netbox_ssot.add_agentcommand"),
            "A rejected immutable review cannot be reopened.",
        )
    if final_review is None and comparison is not None:
        return WorkflowAction(
            "Continue review",
            comparison_url + "#review-actions",
            "get",
            request.user.has_perm("netbox_ssot.view_comparisonrun"),
            "Approve the proposed creates and updates before applying.",
        )
    if approved:
        return WorkflowAction(
            "Apply approved changes" if readiness is None or readiness.ready else "View apply requirements",
            comparison_url + "#apply-actions",
            "get",
            request.user.has_perm("netbox_ssot.view_comparisonrun"),
            (
                "Application performs one atomic transaction."
                if readiness is None or readiness.ready
                else "The approval is recorded, but apply is currently blocked."
            ),
        )
    return None
