from __future__ import annotations

from typing import Any

import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from netbox.tables import NetBoxTable

from .health import agent_health, source_health
from .models import CollectionRun, CollectorAgent, DiscoverySource
from .ui import reconciliation_row


class SourceTable(NetBoxTable):
    name = tables.Column(linkify=lambda record: reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": record.pk}))
    provider_id = tables.Column(verbose_name="Provider")
    health = tables.Column(empty_values=(), orderable=False)
    alignment = tables.Column(empty_values=(), orderable=False)
    latest_collection_at = tables.DateTimeColumn(verbose_name="Last collection")
    assigned_agent = tables.Column(verbose_name="Agent", orderable=True, empty_values=())
    collection_interval_minutes = tables.Column(verbose_name="Interval")

    class Meta(NetBoxTable.Meta):
        model = DiscoverySource
        fields = (
            "name",
            "provider_id",
            "health",
            "alignment",
            "latest_collection_at",
            "collection_interval_minutes",
            "assigned_agent",
            "enabled",
        )
        default_columns = (
            "name",
            "provider_id",
            "health",
            "alignment",
            "latest_collection_at",
            "assigned_agent",
        )
        exclude = ("pk", "id", "actions")

    def render_health(self, record: DiscoverySource) -> str:
        status = source_health(record).status
        return format_html(
            '<span class="badge text-bg-{}">{}</span><div class="small text-secondary mt-1">{}</div>',
            status.color,
            status.label,
            status.detail,
        )

    def render_alignment(self, record: DiscoverySource) -> str:
        if not getattr(record, "latest_comparison_id", None):
            return format_html('<span class="text-secondary">{}</span>', "Not assessed")
        matching = getattr(record, "latest_no_change_count", 0) or 0
        drifted = (getattr(record, "latest_create_count", 0) or 0) + (
            getattr(record, "latest_update_count", 0) or 0
        )
        attention = (getattr(record, "latest_conflict_count", 0) or 0) + (
            getattr(record, "latest_skipped_count", 0) or 0
        )
        total = matching + drifted + attention
        percentage = matching / total * 100 if total else 0
        percentage_label = f"{percentage:.1f}"
        percentage_width = f"{percentage:.2f}"
        return format_html(
            '<div class="d-flex justify-content-between small mb-1">'
            '<span>{} matching</span><span>{} drifted</span></div>'
            '<div class="progress" style="height:.5rem" role="img" aria-label="{}% aligned">'
            '<div class="progress-bar bg-success" style="width:{}%"></div></div>',
            matching,
            drifted,
            percentage_label,
            percentage_width,
        )

    def render_assigned_agent(self, value: CollectorAgent | None) -> str:
        if value is None:
            return format_html('<span class="text-danger">{}</span>', "Not assigned")
        return format_html(
            '<a href="{}">{}</a>',
            reverse("plugins:netbox_ssot:agent_detail", kwargs={"pk": value.pk}),
            value.name,
        )

    def render_collection_interval_minutes(self, value: int) -> str:
        return f"Every {value} min"


class AgentTable(NetBoxTable):
    name = tables.Column(linkify=lambda record: reverse("plugins:netbox_ssot:agent_detail", kwargs={"pk": record.pk}))
    health = tables.Column(empty_values=(), orderable=False)
    source_count = tables.Column(verbose_name="Sources")
    agent_version = tables.Column(verbose_name="Agent version")
    protocol_version = tables.Column(verbose_name="Protocol")
    last_seen_at = tables.DateTimeColumn(verbose_name="Last heartbeat")
    control_interval_seconds = tables.Column(verbose_name="Control interval")

    class Meta(NetBoxTable.Meta):
        model = CollectorAgent
        fields = (
            "name",
            "health",
            "source_count",
            "agent_version",
            "protocol_version",
            "last_seen_at",
            "control_interval_seconds",
            "enabled",
        )
        default_columns = (
            "name",
            "health",
            "source_count",
            "agent_version",
            "protocol_version",
            "last_seen_at",
        )
        exclude = ("pk", "id", "actions")

    def render_health(self, record: CollectorAgent) -> str:
        status = agent_health(record)
        return format_html(
            '<span class="badge text-bg-{}">{}</span><div class="small text-secondary mt-1">{}</div>',
            status.color,
            status.label,
            status.detail,
        )

    def render_source_count(self, value: int) -> str:
        return f"{value} source" + ("s" if value != 1 else "")

    def render_control_interval_seconds(self, value: int) -> str:
        return f"{value}s"


class ReconciliationTable(NetBoxTable):
    source = tables.Column(accessor="source", verbose_name="Source")
    run_id = tables.Column(verbose_name="Collection")
    workflow_status = tables.Column(empty_values=(), verbose_name="State", orderable=False)
    plan = tables.Column(empty_values=(), orderable=False)
    completed_at = tables.DateTimeColumn(verbose_name="Collected")
    observation_count = tables.Column(verbose_name="Observations")
    next_action = tables.Column(
        attrs={
            "th": {"aria-label": "Next action", "class": "text-end"},
            "td": {"class": "text-end text-nowrap noprint p-1"},
        },
        empty_values=(),
        orderable=False,
        verbose_name="",
    )

    class Meta(NetBoxTable.Meta):
        model = CollectionRun
        fields = (
            "source",
            "run_id",
            "workflow_status",
            "plan",
            "completed_at",
            "observation_count",
            "next_action",
            "state",
            "provider_id",
            "agent",
        )
        default_columns = (
            "source",
            "run_id",
            "workflow_status",
            "plan",
            "completed_at",
            "observation_count",
            "next_action",
        )
        exclude = ("pk", "id", "actions")

    def __init__(self, *args: Any, include_comparison: bool = True, **kwargs: Any) -> None:
        self.include_comparison = include_comparison
        super().__init__(*args, **kwargs)

    def render_source(self, value: DiscoverySource) -> str:
        return format_html(
            '<a class="fw-semibold" href="{}">{}</a><div class="small text-secondary">Source → NetBox</div>',
            reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": value.pk}),
            value.name,
        )

    def render_run_id(self, value: Any) -> str:
        return format_html(
            '<a href="{}"><code>{}</code></a>',
            reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": value}),
            value,
        )

    def render_workflow_status(self, record: CollectionRun) -> str:
        row = reconciliation_row(record, include_comparison=self.include_comparison)
        return format_html(
            '<span class="badge text-bg-{}"><i class="mdi {} me-1" aria-hidden="true"></i>{}</span>'
            '<div class="small text-secondary mt-1">{}</div>',
            row.status.tone,
            row.status.icon,
            row.status.label,
            row.detail,
        )

    def render_plan(self, record: CollectionRun) -> str:
        row = reconciliation_row(record, include_comparison=self.include_comparison)
        if row.comparison is None:
            return format_html('<span class="text-secondary">{}</span>', "Not prepared")
        return format_html(
            '<span class="text-success">{} create</span> · <span class="text-warning">{} update</span>'
            '<div class="small text-secondary">{} match · {} attention</div>',
            row.create_count,
            row.update_count,
            row.match_count,
            row.attention_count,
        )

    def render_next_action(self, record: CollectionRun) -> str:
        row = reconciliation_row(record, include_comparison=self.include_comparison)
        button_tone = {
            "attention": "danger",
            "review": "warning",
            "apply": "primary",
        }.get(row.state_group, "secondary")
        icon = {
            "applied": "mdi-receipt-text-outline",
            "rejected": "mdi-clipboard-remove-outline",
            "aligned": "mdi-file-compare",
            "comparison_failed": "mdi-alert-circle-outline",
            "collection_incomplete": "mdi-magnify",
            "blocked": "mdi-alert-octagon-outline",
            "ready_to_apply": "mdi-play",
            "needs_review": "mdi-clipboard-text-search-outline",
            "preparing": "mdi-progress-clock",
            "awaiting_comparison": "mdi-arrow-right",
        }.get(row.status.key, "mdi-arrow-right")
        return format_html(
            '<a class="btn btn-sm btn-{} ssot-table-action" href="{}" title="{}" '
            'aria-label="{}" data-bs-toggle="tooltip"><i class="mdi {}" aria-hidden="true"></i>'
            '<span class="visually-hidden">{}</span></a>',
            button_tone,
            row.action_url,
            row.action_label,
            row.action_label,
            icon,
            row.action_label,
        )


__all__ = ["AgentTable", "ReconciliationTable", "SourceTable"]
