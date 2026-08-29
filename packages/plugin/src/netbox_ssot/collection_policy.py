from __future__ import annotations

from dataclasses import dataclass

from netbox.plugins import get_plugin_config

from .models import ApplyRun, CollectorAgent, ComparisonReview, DiscoverySource

MINIMUM_PAUSE_AGENT_VERSION = (0, 6, 8)


@dataclass(frozen=True, slots=True)
class CollectionSchedulePolicy:
    enabled: bool
    reason: str = ""


def pause_until_resolved_enabled() -> bool:
    return bool(get_plugin_config("netbox_ssot", "pause_scheduled_collections_until_resolved"))


def agent_collection_policy_issue(agent: CollectorAgent) -> str:
    if pause_until_resolved_enabled() and not _agent_version_at_least(
        agent.agent_version,
        MINIMUM_PAUSE_AGENT_VERSION,
    ):
        return f"{agent.name} must be upgraded to agent 0.6.8 or newer for collection review backpressure."
    return ""


def source_collection_schedule_policy(source: DiscoverySource) -> CollectionSchedulePolicy:
    if not pause_until_resolved_enabled():
        return CollectionSchedulePolicy(enabled=True)

    latest_run = source.runs.filter(state="complete").order_by("-received_at").first()
    if latest_run is None:
        return CollectionSchedulePolicy(enabled=True)

    latest_comparison = latest_run.comparisons.order_by("-created_at").first()
    if latest_comparison is None:
        return CollectionSchedulePolicy(
            enabled=False,
            reason=f"Collection {latest_run.run_id} is waiting for review.",
        )
    if ApplyRun.objects.filter(comparison=latest_comparison).exists():
        return CollectionSchedulePolicy(enabled=True)
    final_review = ComparisonReview.objects.filter(comparison=latest_comparison).first()
    if final_review is not None:
        if final_review.decision == ComparisonReview.Decision.REJECTED:
            return CollectionSchedulePolicy(enabled=True)
        return CollectionSchedulePolicy(
            enabled=False,
            reason=f"Review {latest_comparison.id} is approved and waiting for apply.",
        )
    unresolved_count = (
        latest_comparison.create_count
        + latest_comparison.update_count
        + latest_comparison.conflict_count
        + latest_comparison.skipped_count
    )
    if unresolved_count:
        return CollectionSchedulePolicy(
            enabled=False,
            reason=f"Review {latest_comparison.id} has {unresolved_count} unresolved record(s).",
        )
    return CollectionSchedulePolicy(enabled=True)


def _agent_version_at_least(value: str, required: tuple[int, int, int]) -> bool:
    try:
        parts = tuple(int(part) for part in value.split("-", 1)[0].split("."))
    except (AttributeError, ValueError):
        return False
    return len(parts) == 3 and parts >= required


__all__ = [
    "CollectionSchedulePolicy",
    "agent_collection_policy_issue",
    "pause_until_resolved_enabled",
    "source_collection_schedule_policy",
]
