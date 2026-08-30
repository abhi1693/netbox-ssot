from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import django_filters
from django.db.models import Q, QuerySet
from django.utils import timezone
from netbox.filtersets import BaseFilterSet

from .models import (
    CollectionRun,
    CollectorAgent,
    ComparisonPreparation,
    ComparisonReview,
    DiscoverySource,
    SynchronizationDirection,
)
from .ui import RECONCILIATION_STATES


class SourceFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method="search", label="Search")
    assigned_agent = django_filters.ModelMultipleChoiceFilter(queryset=CollectorAgent.objects.all())

    class Meta:
        model = DiscoverySource
        fields = ("q", "provider_id", "enabled", "assigned_agent")

    def search(self, queryset: QuerySet[DiscoverySource], _name: str, value: str) -> QuerySet[DiscoverySource]:
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(provider_id__icontains=value))


class AgentFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method="search", label="Search")

    class Meta:
        model = CollectorAgent
        fields = ("q", "enabled", "agent_version", "protocol_version")

    def search(self, queryset: QuerySet[CollectorAgent], _name: str, value: str) -> QuerySet[CollectorAgent]:
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) | Q(agent_version__icontains=value) | Q(protocol_version__icontains=value)
        )


class ReconciliationFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method="search", label="Search")
    source = django_filters.ModelMultipleChoiceFilter(queryset=DiscoverySource.objects.all())
    agent = django_filters.ModelMultipleChoiceFilter(queryset=CollectorAgent.objects.all())
    workflow_state = django_filters.ChoiceFilter(choices=RECONCILIATION_STATES, method="filter_workflow_state")
    period = django_filters.ChoiceFilter(
        choices=(("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days")),
        method="filter_period",
    )
    direction = django_filters.ChoiceFilter(
        choices=SynchronizationDirection.choices,
        method="filter_direction",
    )

    class Meta:
        model = CollectionRun
        fields = ("q", "source", "workflow_state", "period", "direction", "state", "provider_id", "agent")

    def search(self, queryset: QuerySet[CollectionRun], _name: str, value: str) -> QuerySet[CollectionRun]:
        if not value.strip():
            return queryset
        query = Q(source__name__icontains=value) | Q(provider_id__icontains=value)
        try:
            run_id = UUID(value)
        except (TypeError, ValueError):
            run_id = None
        if run_id is not None:
            query |= Q(run_id=run_id)
        return queryset.filter(query)

    def filter_period(self, queryset: QuerySet[CollectionRun], _name: str, value: str) -> QuerySet[CollectionRun]:
        if value not in {"7", "30", "90"}:
            return queryset
        return queryset.filter(received_at__gte=timezone.now() - timedelta(days=int(value)))

    def filter_direction(self, queryset: QuerySet[CollectionRun], _name: str, value: str) -> QuerySet[CollectionRun]:
        if value not in SynchronizationDirection.values:
            return queryset
        return queryset.filter(ui_latest_direction=value)

    def filter_workflow_state(
        self,
        queryset: QuerySet[CollectionRun],
        _name: str,
        value: str,
    ) -> QuerySet[CollectionRun]:
        attention_query = (
            ~Q(state="complete")
            | Q(comparison_preparation__state=ComparisonPreparation.State.FAILED)
            | Q(ui_latest_conflict_count__gt=0)
            | Q(ui_latest_skipped_count__gt=0)
        )
        review_query = (
            Q(
                ui_latest_comparison_id__isnull=False,
                ui_latest_apply_id__isnull=True,
                ui_latest_review_decision__isnull=True,
                ui_latest_conflict_count=0,
                ui_latest_skipped_count=0,
            )
            & (Q(ui_latest_create_count__gt=0) | Q(ui_latest_update_count__gt=0))
        )
        apply_query = Q(
            ui_latest_review_decision=ComparisonReview.Decision.APPROVED,
            ui_latest_apply_id__isnull=True,
        )
        if value == "action_required":
            return queryset.filter(attention_query | review_query | apply_query).distinct()
        if value == "attention":
            return queryset.filter(attention_query).distinct()
        if value == "review":
            return queryset.filter(review_query)
        if value == "apply":
            return queryset.filter(apply_query)
        if value == "processing":
            return queryset.filter(state="complete", ui_latest_comparison_id__isnull=True).exclude(
                comparison_preparation__state=ComparisonPreparation.State.FAILED
            )
        if value == "complete":
            return queryset.filter(
                Q(ui_latest_apply_id__isnull=False)
                | Q(ui_latest_review_decision=ComparisonReview.Decision.REJECTED)
                | Q(
                    ui_latest_create_count=0,
                    ui_latest_update_count=0,
                    ui_latest_conflict_count=0,
                    ui_latest_skipped_count=0,
                    ui_latest_comparison_id__isnull=False,
                )
            ).distinct()
        return queryset


__all__ = ["AgentFilterSet", "ReconciliationFilterSet", "SourceFilterSet"]
