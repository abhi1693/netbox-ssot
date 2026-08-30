from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from netbox_ssot.forms import AgentFilterForm, ReconciliationFilterForm, SourceFilterForm
from netbox_ssot.models import (
    AgentCommand,
    AgentEnrollmentToken,
    AgentSecurityEvent,
    AgentSigningKey,
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

    def test_agent_detail_uses_overview_tab_and_native_edit_action(self) -> None:
        detail_url = reverse("plugins:netbox_ssot:agent_detail", kwargs={"pk": self.agent.pk})
        settings_url = reverse("plugins:netbox_ssot:agent_edit", kwargs={"pk": self.agent.pk})
        delete_url = reverse("plugins:netbox_ssot:agent_delete", kwargs={"pk": self.agent.pk})
        replace_url = reverse("plugins:netbox_ssot:agent_replace", kwargs={"pk": self.agent.pk})
        revoke_url = reverse("plugins:netbox_ssot:agent_revoke_keys", kwargs={"pk": self.agent.pk})
        detail = self.client.get(detail_url)

        assert detail.status_code == 200
        self.assertTemplateUsed(detail, "generic/_base.html")
        self.assertContains(detail, 'class="nav nav-tabs"', count=1)
        self.assertContains(
            detail,
            f'class="nav-link active" href="{detail_url}"',
            count=1,
        )
        self.assertContains(
            detail,
            f'href="{settings_url}"',
        )
        self.assertNotContains(detail, ">Settings</a>")
        self.assertContains(detail, f'href="{delete_url}"')
        self.assertContains(detail, f'href="{replace_url}"')
        self.assertContains(detail, f'action="{revoke_url}"')

        settings = self.client.get(settings_url)

        assert settings.status_code == 200
        self.assertTemplateUsed(settings, "generic/_base.html")
        self.assertNotContains(settings, 'class="nav nav-tabs"')
        self.assertNotContains(settings, "Replace agent identity")
        self.assertNotContains(settings, "Revoke agent access")

    def test_never_connected_agent_shows_start_command_instead_of_empty_runtime_sections(self) -> None:
        detail_url = reverse("plugins:netbox_ssot:agent_detail", kwargs={"pk": self.agent.pk})

        waiting = self.client.get(detail_url)

        assert waiting.status_code == 200
        self.assertContains(waiting, "Start this agent")
        self.assertContains(waiting, 'id="agent-runtime-command"')
        self.assertContains(waiting, str(self.agent.pk))
        self.assertContains(waiting, "--allow-insecure-http")
        self.assertNotContains(waiting, "No assigned sources")
        self.assertNotContains(waiting, "No agent actions have been requested")

        self.agent.last_seen_at = timezone.now()
        self.agent.save(update_fields=("last_seen_at",))
        connected = self.client.get(detail_url)

        assert connected.status_code == 200
        self.assertNotContains(connected, "Start this agent")
        self.assertContains(connected, "Assigned sources")
        self.assertContains(connected, "Runtime command")

    def test_unused_enrolled_agent_can_be_deleted_with_native_confirmation(self) -> None:
        agent = CollectorAgent.objects.create(name="Disposable collector", public_key="C" * 43)
        source = DiscoverySource.objects.create(
            name="Disposable collector source",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
            assigned_agent=agent,
        )
        key = AgentSigningKey.objects.create(agent=agent, public_key="D" * 43)
        enrollment = AgentEnrollmentToken.objects.create(
            token_hash="e" * 64,
            token_prefix="nbxssot_delete",
            agent_name=agent.name,
            created_by=self.user,
            expires_at=timezone.now() + timedelta(minutes=15),
            used_at=timezone.now(),
            enrolled_agent=agent,
        )
        event = AgentSecurityEvent.objects.create(
            agent=agent,
            kind=AgentSecurityEvent.Kind.ENROLLED,
            actor=self.user,
        )
        delete_url = reverse("plugins:netbox_ssot:agent_delete", kwargs={"pk": agent.pk})

        confirmation = self.client.get(delete_url)

        assert confirmation.status_code == 200
        self.assertTemplateUsed(confirmation, "generic/object_delete.html")
        self.assertContains(confirmation, "Confirm Deletion")
        self.assertContains(confirmation, "Disposable collector")

        response = self.client.post(delete_url, {"confirm": True})

        assert response.status_code == 302
        assert response.url == reverse("plugins:netbox_ssot:agent_list")
        assert not CollectorAgent.objects.filter(pk=agent.pk).exists()
        assert not AgentSigningKey.objects.filter(pk=key.pk).exists()
        assert not AgentEnrollmentToken.objects.filter(pk=enrollment.pk).exists()
        event.refresh_from_db()
        assert event.agent is None
        source.refresh_from_db()
        assert source.assigned_agent is None

    def test_agent_with_retained_collection_history_is_protected_from_deletion(self) -> None:
        delete_url = reverse("plugins:netbox_ssot:agent_delete", kwargs={"pk": self.agent.pk})

        response = self.client.get(delete_url)

        assert response.status_code == 302
        assert response.url == self.agent.get_absolute_url()
        assert CollectorAgent.objects.filter(pk=self.agent.pk).exists()

    def test_agent_deletion_requires_delete_permission(self) -> None:
        user = get_user_model().objects.create_user(username=f"agent-delete-denied-{uuid4()}")
        self.client.force_login(user)

        response = self.client.get(reverse("plugins:netbox_ssot:agent_delete", kwargs={"pk": self.agent.pk}))

        assert response.status_code == 403

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

    def test_overview_and_activity_share_native_header_tabs(self) -> None:
        overview = self.client.get(reverse("plugins:netbox_ssot:overview"))

        assert overview.status_code == 200
        self.assertTemplateUsed(overview, "generic/_base.html")
        self.assertContains(overview, 'class="nav nav-tabs"', count=1)
        self.assertContains(
            overview,
            f'class="nav-link active" href="{reverse("plugins:netbox_ssot:overview")}"',
            count=1,
        )
        self.assertContains(
            overview,
            f'class="nav-link" href="{reverse("plugins:netbox_ssot:summary")}"',
            count=1,
        )
        self.assertContains(overview, "mdi mdi-history me-1", count=1)
        self.assertNotContains(overview, "?view=")
        self.assertNotContains(overview, "ssot-mode-tabs")

        activity = self.client.get(reverse("plugins:netbox_ssot:activity"))

        assert activity.status_code == 200
        self.assertTemplateUsed(activity, "generic/_base.html")
        self.assertContains(activity, 'class="nav nav-tabs"', count=1)
        self.assertContains(activity, 'class="nav-link active"', count=1)
        self.assertNotContains(activity, "?view=")
        self.assertNotContains(activity, "ssot-mode-tabs")

    def test_provider_catalog_is_a_compact_provider_selector(self) -> None:
        response = self.client.get(reverse("plugins:netbox_ssot:provider_list"))

        assert response.status_code == 200
        self.assertContains(response, "Choose a provider to configure a new synchronization source.")
        self.assertContains(response, "NetBox")
        self.assertContains(response, "Version 0.0.1")
        self.assertContains(response, "mdi mdi-cube-outline")
        self.assertContains(
            response,
            "An infrastructure resource modeling platform for documenting and managing network infrastructure.",
        )
        self.assertContains(response, "Configure source")
        self.assertNotContains(response, "Technical details")
        self.assertNotContains(response, "Learn more")
        self.assertNotContains(response, "Connect agent")

    def test_source_wizard_groups_and_filters_provider_datasets(self) -> None:
        response = self.client.get(reverse("plugins:netbox_ssot:source_wizard", kwargs={"provider_id": "netbox"}))

        assert response.status_code == 200
        self.assertTemplateUsed(response, "generic/_base.html")
        groups = response.context["dataset_groups"]
        assert [group.title for group in groups] == [
            "Tenancy",
            "IPAM",
            "Core",
            "Users",
            "Extras",
            "DCIM",
            "Virtualization",
            "VPN",
            "Wireless",
            "Circuits",
        ]
        assert sum(len(group.datasets) for group in groups) == 38
        self.assertContains(response, 'id="dataset-search"', count=1)
        self.assertContains(response, '<div class="accordion-item" data-dataset-group', count=10, html=False)
        self.assertContains(response, 'data-dataset-action="select-all"', count=1)
        self.assertContains(response, 'data-dataset-action="clear-all"', count=1)
        self.assertContains(response, "object-edit object-edit--with-sticky-actions")
        self.assertContains(response, 'class="sticky-actions sticky-actions-footer d-print-none"', count=1)
        self.assertNotContains(response, 'id="source-form-tab"')
        self.assertContains(response, 'class="col-sm-3 col-form-label text-lg-end required"')
        self.assertNotContains(response, "Provider → Source configuration → Agent")
        self.assertNotContains(response, '<div class="card mt-3">', html=False)
        self.assertContains(
            response,
            '<input class="form-check-input flex-shrink-0 mt-1" type="checkbox" name="datasets"',
            count=38,
            html=False,
        )

    def test_source_edit_uses_native_edit_header_and_actions(self) -> None:
        response = self.client.get(reverse("plugins:netbox_ssot:source_edit", kwargs={"pk": self.source.pk}))

        assert response.status_code == 200
        assert response.context["editing"] is True
        self.assertContains(response, f"Editing source {self.source.name}")
        self.assertNotContains(response, 'id="source-form-tab"')
        self.assertContains(response, ">Save</button>", count=1, html=False)
        self.assertContains(response, ">Cancel</a>", count=1, html=False)
        self.assertContains(response, "Enable automatic collection")

    def test_source_detail_uses_native_object_page_patterns(self) -> None:
        response = self.client.get(reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": self.source.pk}))

        assert response.status_code == 200
        self.assertTemplateUsed(response, "generic/_base.html")
        self.assertContains(response, 'class="page-header m-0"', count=1)
        self.assertContains(response, 'class="nav nav-tabs"', count=1)
        self.assertContains(response, 'class="table table-hover attr-table mb-0"', count=1)
        self.assertContains(response, "Data scope")
        self.assertContains(response, "Application")
        self.assertContains(response, "DCIM")
        self.assertContains(response, "mdi mdi-pencil", count=1)
        self.assertContains(response, "mdi mdi-trash-can-outline", count=1)
        self.assertContains(
            response,
            reverse("plugins:netbox_ssot:source_data", kwargs={"pk": self.source.pk}),
        )
        self.assertContains(
            response,
            reverse("plugins:netbox_ssot:source_history", kwargs={"pk": self.source.pk}),
        )
        self.assertNotContains(response, "?tab=")
        self.assertNotContains(response, "View model mappings")
        self.assertNotContains(response, "ssot-mode-tabs")
        self.assertNotContains(response, "Source settings")

    def test_failed_collection_banner_displays_recorded_errors(self) -> None:
        self.agent.last_seen_at = timezone.now()
        self.agent.agent_version = "0.0.1"
        self.agent.provider_capabilities = [
            {
                "provider_id": "netbox",
                "implementation_version": "0.0.1",
                "contract_version": "1.0",
            }
        ]
        self.agent.save(update_fields=("last_seen_at", "agent_version", "provider_capabilities"))
        failed_run = CollectionRun.objects.create(
            run_id=uuid4(),
            source=self.source,
            agent=self.agent,
            provider_id="netbox",
            provider_version="0.0.1",
            contract_version="1.0",
            state="failed",
            started_at=timezone.now(),
            completed_at=timezone.now(),
            datasets=["regions"],
            scope=[],
            messages=[
                {
                    "code": "collection_complete",
                    "message": "All selected pages were collected.",
                    "retryable": False,
                },
                {
                    "code": "authentication_failed",
                    "message": "Remote NetBox returned HTTP 401 for /api/dcim/sites/.",
                    "retryable": False,
                },
            ],
            completeness_token="failed",
            payload_digest="f" * 64,
            observation_count=0,
        )

        response = self.client.get(reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": self.source.pk}))

        assert response.status_code == 200
        self.assertContains(response, "authentication_failed")
        self.assertContains(response, "Remote NetBox returned HTTP 401 for /api/dcim/sites/.", count=2)
        self.assertNotContains(response, "All selected pages were collected.")
        self.assertNotContains(response, "The latest collection did not complete.")
        self.assertContains(
            response,
            reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": failed_run.pk}),
        )

        source_list = self.client.get(
            reverse("plugins:netbox_ssot:source_list"),
            {"q": self.source.name},
        )
        assert source_list.status_code == 200
        self.assertContains(source_list, "Remote NetBox returned HTTP 401 for /api/dcim/sites/.")

    def test_source_status_polls_and_displays_live_collection_activity(self) -> None:
        now = timezone.now()
        self.agent.last_seen_at = now
        self.agent.agent_version = "0.0.1"
        self.agent.provider_capabilities = [
            {
                "provider_id": "netbox",
                "implementation_version": "0.0.1",
                "contract_version": "1.0",
            }
        ]
        self.agent.save(update_fields=("last_seen_at", "agent_version", "provider_capabilities"))
        self.source.active_collection_started_at = now - timedelta(seconds=8)
        self.source.active_collection_seen_at = now
        self.source.save(update_fields=("active_collection_started_at", "active_collection_seen_at"))
        detail_url = reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": self.source.pk})
        status_url = reverse("plugins:netbox_ssot:source_status", kwargs={"pk": self.source.pk})

        detail = self.client.get(detail_url)

        assert detail.status_code == 200
        self.assertContains(detail, "Collection running")
        self.assertContains(detail, 'class="mdi mdi-loading mdi-spin fs-3"')
        self.assertContains(detail, f'hx-get="{status_url}"')
        self.assertContains(detail, 'hx-trigger="every 5s"')

        status = self.client.get(status_url)

        assert status.status_code == 200
        self.assertTemplateUsed(status, "netbox_ssot/_source_status.html")
        self.assertContains(status, "Collection running")
        self.assertContains(status, "started")
        self.assertContains(status, 'id="source-health-badge"')
        self.assertContains(status, 'hx-swap-oob="true"')

        self.source.active_collection_seen_at = now - timedelta(minutes=1)
        self.source.save(update_fields=("active_collection_seen_at",))
        stale_status = self.client.get(status_url)

        assert stale_status.status_code == 200
        self.assertNotContains(stale_status, "Collection running")
        self.assertNotContains(stale_status, "mdi-loading mdi-spin")

    def test_recent_commands_poll_until_the_agent_reports_a_terminal_result(self) -> None:
        command = AgentCommand.objects.create(
            agent=self.agent,
            source=self.source,
            kind=AgentCommand.Kind.TEST_CONNECTION,
            requested_by=self.user,
        )
        detail_url = reverse("plugins:netbox_ssot:source_detail", kwargs={"pk": self.source.pk})
        commands_url = reverse("plugins:netbox_ssot:source_commands", kwargs={"pk": self.source.pk})

        detail = self.client.get(detail_url)

        assert detail.status_code == 200
        self.assertContains(detail, 'id="source-recent-commands"')
        self.assertContains(detail, f'hx-get="{commands_url}"')
        self.assertContains(detail, 'hx-trigger="every 2s"')
        self.assertContains(detail, "Waiting for agent")
        self.assertContains(detail, "Pending")

        AgentCommand.objects.filter(pk=command.pk).update(
            state=AgentCommand.State.FAILED,
            completed_at=timezone.now(),
            result={"summary": "NetBox returned HTTP 403."},
        )
        terminal = self.client.get(commands_url)

        assert terminal.status_code == 200
        self.assertTemplateUsed(terminal, "netbox_ssot/_source_commands.html")
        self.assertContains(terminal, "Failed")
        self.assertContains(terminal, "NetBox returned HTTP 403.")
        self.assertNotContains(terminal, "hx-trigger")
        self.assertNotContains(terminal, "Waiting for agent")

    def test_source_tabs_use_canonical_routes(self) -> None:
        data = self.client.get(reverse("plugins:netbox_ssot:source_data", kwargs={"pk": self.source.pk}))
        history = self.client.get(reverse("plugins:netbox_ssot:source_history", kwargs={"pk": self.source.pk}))

        assert data.status_code == 200
        assert data.context["selected_tab"] == "data"
        self.assertContains(data, 'class="nav-link active"', count=1)
        self.assertContains(data, "Data ownership")
        self.assertNotContains(data, "?tab=")
        assert history.status_code == 200
        assert history.context["selected_tab"] == "history"
        self.assertContains(history, 'class="nav-link active"', count=1)
        self.assertContains(history, "Retention")
        self.assertNotContains(history, "?tab=")

    def test_unused_source_can_be_deleted_with_native_confirmation(self) -> None:
        source = DiscoverySource.objects.create(
            name="Disposable source",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
        )
        delete_url = reverse("plugins:netbox_ssot:source_delete", kwargs={"pk": source.pk})

        confirmation = self.client.get(delete_url)

        assert confirmation.status_code == 200
        self.assertTemplateUsed(confirmation, "generic/object_delete.html")
        self.assertContains(confirmation, "Confirm Deletion")
        self.assertContains(confirmation, "Disposable source")

        response = self.client.post(delete_url, {"confirm": True})

        assert response.status_code == 302
        assert response.url == reverse("plugins:netbox_ssot:source_list")
        assert not DiscoverySource.objects.filter(pk=source.pk).exists()

    def test_source_with_retained_history_is_protected_from_deletion(self) -> None:
        delete_url = reverse("plugins:netbox_ssot:source_delete", kwargs={"pk": self.source.pk})

        response = self.client.get(delete_url)

        assert response.status_code == 302
        assert response.url == self.source.get_absolute_url()
        assert DiscoverySource.objects.filter(pk=self.source.pk).exists()

    def test_source_deletion_requires_delete_permission(self) -> None:
        user = get_user_model().objects.create_user(username=f"source-delete-denied-{uuid4()}")
        self.client.force_login(user)

        response = self.client.get(reverse("plugins:netbox_ssot:source_delete", kwargs={"pk": self.source.pk}))

        assert response.status_code == 403


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
