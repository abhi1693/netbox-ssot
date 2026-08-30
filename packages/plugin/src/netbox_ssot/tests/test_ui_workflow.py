from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from netbox_ssot.forms import AgentFilterForm, ReconciliationFilterForm, SourceFilterForm
from netbox_ssot.models import (
    CollectionRun,
    CollectorAgent,
    ComparisonPreparation,
    ComparisonRun,
    DiscoverySource,
)
from netbox_ssot.tables import AgentTable, ReconciliationTable, SourceTable
from netbox_ssot.ui import build_alignment_trend, reconciliation_row
from netbox_ssot.views import _reconciliation_queryset


class NativeListUITests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create_superuser(
            username=f"ui-{uuid4()}",
            password="unused",
        )
        cls.agent = CollectorAgent.objects.create(
            name="UI collector",
            public_key="A" * 43,
            agent_version="0.0.1",
            protocol_version="1.0",
        )
        cls.source = DiscoverySource.objects.create(
            name="UI source",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
            assigned_agent=cls.agent,
        )
        cls.collection_run = CollectionRun.objects.create(
            run_id=uuid4(),
            source=cls.source,
            agent=cls.agent,
            provider_id="netbox",
            provider_version="0.0.1",
            contract_version="1.0",
            state="failed",
            started_at=timezone.now(),
            completed_at=timezone.now(),
            datasets=["regions"],
            scope=[],
            messages=[],
            completeness_token="complete",
            payload_digest="a" * 64,
            observation_count=3,
        )

    def setUp(self) -> None:
        self.client.force_login(self.user)

    def test_source_inventory_uses_netbox_table_and_filter_form(self) -> None:
        response = self.client.get(reverse("plugins:netbox_ssot:source_list"), {"q": "UI source"})

        assert response.status_code == 200
        assert isinstance(response.context["table"], SourceTable)
        assert isinstance(response.context["filter_form"], SourceFilterForm)
        self.assertTemplateUsed(response, "generic/object_list.html")
        self.assertContains(response, "Filters")
        self.assertContains(response, "UI source")

    def test_agent_inventory_uses_netbox_table_and_filter_form(self) -> None:
        response = self.client.get(reverse("plugins:netbox_ssot:agent_list"), {"q": "UI collector"})

        assert response.status_code == 200
        assert isinstance(response.context["table"], AgentTable)
        assert isinstance(response.context["filter_form"], AgentFilterForm)
        self.assertContains(response, "UI collector")

    def test_reconciliation_inventory_has_visible_default_period_filter(self) -> None:
        url = reverse("plugins:netbox_ssot:reconciliation_list")

        redirect_response = self.client.get(url)
        assert redirect_response.status_code == 302
        assert redirect_response.url == f"{url}?period=30&workflow_state=action_required"

        response = self.client.get(redirect_response.url)
        assert response.status_code == 200
        assert isinstance(response.context["table"], ReconciliationTable)
        assert isinstance(response.context["filter_form"], ReconciliationFilterForm)
        self.assertContains(response, "UI source")
        self.assertContains(response, "Action required")
        self.assertContains(response, "Last 30 days")
        self.assertContains(response, "ssot-table-action")
        self.assertContains(response, '<span class="visually-hidden">Inspect evidence</span>', html=True)
        self.assertNotContains(response, "btn-outline-primary text-nowrap")


class WorkflowPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.agent = CollectorAgent.objects.create(name="Workflow collector", public_key="B" * 43)
        cls.source = DiscoverySource.objects.create(
            name="Workflow source",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
            assigned_agent=cls.agent,
        )
        cls.collection_run = CollectionRun.objects.create(
            run_id=uuid4(),
            source=cls.source,
            agent=cls.agent,
            provider_id="netbox",
            provider_version="0.0.1",
            contract_version="1.0",
            state="complete",
            started_at=timezone.now(),
            completed_at=timezone.now(),
            datasets=["regions"],
            scope=[],
            messages=[],
            completeness_token="complete",
            payload_digest="b" * 64,
            observation_count=3,
        )
        cls.comparison = ComparisonRun.objects.create(
            collection_run=cls.collection_run,
            source_payload_digest=cls.collection_run.payload_digest,
            target_snapshot_digest="c" * 64,
            engine_version="1.0",
            no_change_count=3,
        )
        ComparisonPreparation.objects.create(
            collection_run=cls.collection_run,
            comparison=cls.comparison,
            state=ComparisonPreparation.State.COMPLETED,
            completed_at=timezone.now(),
        )

    def test_no_change_reconciliation_is_complete(self) -> None:
        run = _reconciliation_queryset().get(pk=self.collection_run.pk)

        row = reconciliation_row(run)

        assert row.status.key == "aligned"
        assert row.state_group == "complete"
        assert row.action_label == "View comparison"

    def test_alignment_trend_carries_latest_source_snapshot_forward(self) -> None:
        observed_at = timezone.now() - timedelta(days=2)
        ComparisonRun.objects.filter(pk=self.comparison.pk).update(created_at=observed_at)
        self.comparison.refresh_from_db()

        trend = build_alignment_trend(
            (self.source,),
            (self.comparison,),
            days=3,
            now=timezone.now(),
        )

        assert len(trend.points) == 3
        assert trend.points[-1].alignment == 100
        assert trend.points[-1].assessed_sources == 1
        assert trend.points[0].x == 2
        assert trend.points[-1].x == 98
        assert trend.latest == trend.points[-1]
        assert trend.area
        assert trend.polyline

    def test_alignment_trend_does_not_plot_empty_comparisons_as_zero_percent(self) -> None:
        ComparisonRun.objects.filter(pk=self.comparison.pk).update(no_change_count=0)
        self.comparison.refresh_from_db()

        trend = build_alignment_trend(
            (self.source,),
            (self.comparison,),
            days=3,
            now=timezone.now(),
        )

        assert trend.points == ()
        assert trend.polyline == ""
        assert trend.area == ""
