from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from .agent_capabilities import source_capability_issue
from .collection_policy import agent_collection_policy_issue, source_collection_schedule_policy
from .models import CollectorAgent, DiscoverySource


@dataclass(frozen=True)
class HealthStatus:
    key: str
    label: str
    color: str
    detail: str


@dataclass(frozen=True)
class SourceHealth:
    status: HealthStatus
    last_success_at: datetime | None
    next_expected_at: datetime | None


def agent_health(agent: CollectorAgent, *, now: datetime | None = None) -> HealthStatus:
    now = now or timezone.now()
    if not agent.enabled:
        return HealthStatus("disabled", "Disabled", "secondary", "Agent is disabled.")
    if agent.last_seen_at is None:
        return HealthStatus("offline", "Never connected", "danger", "No heartbeat has been received.")
    age = now - agent.last_seen_at
    effective_interval = agent.reported_control_interval_seconds or agent.control_interval_seconds
    online_threshold = timedelta(seconds=max(15, effective_interval * 3))
    stale_threshold = timedelta(seconds=max(120, effective_interval * 10))
    if age <= online_threshold:
        return HealthStatus("online", "Online", "success", "Heartbeat is current.")
    if age <= stale_threshold:
        return HealthStatus("stale", "Stale", "warning", "Heartbeat is delayed.")
    return HealthStatus("offline", "Offline", "danger", "The agent has missed multiple expected heartbeats.")


def source_health(source: DiscoverySource, *, now: datetime | None = None) -> SourceHealth:
    now = now or timezone.now()
    latest_at = getattr(source, "latest_collection_at", None)
    latest_state = getattr(source, "latest_collection_state", None)
    last_success_at = getattr(source, "last_success_at", None)
    next_expected_at = None
    if latest_at is not None:
        next_expected_at = latest_at + timedelta(minutes=source.collection_interval_minutes)
    schedule_policy = source_collection_schedule_policy(source)
    if not schedule_policy.enabled:
        next_expected_at = None

    if not source.enabled:
        status = HealthStatus("disabled", "Disabled", "secondary", "Scheduled collection is disabled.")
    elif source.assigned_agent is None:
        status = HealthStatus("needs_agent", "Needs agent", "warning", "Assign an agent to collect this source.")
    else:
        agent_status = agent_health(source.assigned_agent, now=now)
        capability_problem = source_capability_issue(source.assigned_agent, source.provider_id)
        collection_policy_problem = agent_collection_policy_issue(source.assigned_agent)
        if capability_problem or collection_policy_problem:
            status = HealthStatus(
                "incompatible_agent",
                "Incompatible agent",
                "danger",
                capability_problem or collection_policy_problem,
            )
        elif agent_status.key == "disabled":
            status = HealthStatus("agent_disabled", "Agent disabled", "danger", agent_status.detail)
        elif agent_status.key == "offline":
            status = HealthStatus("agent_offline", "Agent offline", "danger", agent_status.detail)
        elif agent_status.key == "stale":
            status = HealthStatus("agent_stale", "Agent stale", "warning", agent_status.detail)
        elif not schedule_policy.enabled:
            status = HealthStatus("waiting_review", "Waiting for review", "info", schedule_policy.reason)
        elif latest_at is None:
            status = HealthStatus(
                "waiting",
                "Waiting for data",
                "info",
                "The agent is online and the first collection is pending.",
            )
        elif latest_state != "complete":
            status = HealthStatus("failed", "Last run failed", "danger", "The latest collection did not complete.")
        else:
            grace = max(timedelta(minutes=1), timedelta(minutes=source.collection_interval_minutes / 10))
            if next_expected_at is not None and now > next_expected_at + grace:
                status = HealthStatus("overdue", "Overdue", "danger", "The next scheduled collection is late.")
            else:
                status = HealthStatus("healthy", "Healthy", "success", "Agent and collection schedule are current.")
    return SourceHealth(status=status, last_success_at=last_success_at, next_expected_at=next_expected_at)
