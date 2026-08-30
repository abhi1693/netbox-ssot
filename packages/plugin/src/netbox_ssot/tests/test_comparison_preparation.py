from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from netbox_ssot.api.views import BatchIngestView
from netbox_ssot.models import (
    CollectionRun,
    CollectorAgent,
    ComparisonPreparation,
    ComparisonRun,
    DiscoverySource,
)
from netbox_ssot.planning.comparison import ENGINE_VERSION
from netbox_ssot.planning.service import ComparisonOutcome
from netbox_ssot.preparation import prepare_comparison, request_comparison_preparation
from netbox_ssot.providers import ProviderRegistry
from netbox_ssot_contracts import ObservationBatch, selected_dataset_ids


class ComparisonPreparationTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        suffix = uuid4().hex
        cls.user = get_user_model().objects.create_superuser(
            username=f"comparison-preparation-{suffix}",
            password="unused",
        )
        cls.agent = CollectorAgent.objects.create(
            name=f"comparison-preparation-agent-{suffix}",
            public_key="A" * 43,
        )
        cls.source = DiscoverySource.objects.create(
            name=f"comparison-preparation-source-{suffix}",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
            assigned_agent=cls.agent,
        )

    def setUp(self) -> None:
        self.client.force_login(self.user)
        self.run = CollectionRun.objects.create(
            run_id=uuid4(),
            source=self.source,
            agent=self.agent,
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
            payload_digest=uuid4().hex * 2,
            observation_count=10,
        )

    @patch("netbox_ssot.jobs.PrepareComparisonJob.enqueue")
    def test_request_queues_one_idempotent_background_job(self, enqueue) -> None:
        job_id = uuid4()
        enqueue.return_value = SimpleNamespace(job_id=job_id)

        first = request_comparison_preparation(self.run)
        second = request_comparison_preparation(self.run)

        assert first.queued
        assert not second.queued
        assert first.preparation.state == ComparisonPreparation.State.PENDING
        assert first.preparation.job_id == job_id
        assert first.preparation.attempt_count == 1
        enqueue.assert_called_once_with(
            preparation_id=str(first.preparation.pk),
            notifications="never",
            job_timeout=86_400,
        )

    @patch("netbox_ssot.jobs.PrepareComparisonJob.enqueue", side_effect=RuntimeError("Redis unavailable"))
    def test_queue_failure_is_durable_and_retryable(self, enqueue) -> None:
        outcome = request_comparison_preparation(self.run)

        assert not outcome.queued
        assert outcome.preparation.state == ComparisonPreparation.State.FAILED
        assert outcome.preparation.error == "Redis unavailable"

    def test_worker_persists_the_prepared_comparison(self) -> None:
        preparation = ComparisonPreparation.objects.create(
            collection_run=self.run,
            state=ComparisonPreparation.State.PENDING,
            attempt_count=1,
        )
        comparison = ComparisonRun.objects.create(
            collection_run=self.run,
            source_payload_digest=self.run.payload_digest,
            target_snapshot_digest="b" * 64,
            engine_version=ENGINE_VERSION,
            create_count=2,
            update_count=1,
            no_change_count=6,
            conflict_count=1,
        )

        with patch(
            "netbox_ssot.preparation.create_comparison",
            return_value=ComparisonOutcome(comparison, True),
        ):
            completed = prepare_comparison(preparation.pk)

        assert completed.state == ComparisonPreparation.State.COMPLETED
        assert completed.comparison == comparison
        assert completed.started_at is not None
        assert completed.completed_at is not None

    @patch("netbox_ssot.jobs.PrepareComparisonJob.enqueue")
    def test_manual_compare_returns_immediately_to_background_status(self, enqueue) -> None:
        enqueue.return_value = SimpleNamespace(job_id=uuid4())

        response = self.client.post(
            reverse("plugins:netbox_ssot:comparison_add", kwargs={"pk": self.run.pk})
        )

        assert response.status_code == 302
        assert response.url == reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": self.run.pk})
        assert not ComparisonRun.objects.filter(collection_run=self.run).exists()
        assert ComparisonPreparation.objects.get(collection_run=self.run).state == ComparisonPreparation.State.PENDING

    def test_collection_page_explains_background_preparation(self) -> None:
        ComparisonPreparation.objects.create(
            collection_run=self.run,
            state=ComparisonPreparation.State.RUNNING,
            attempt_count=1,
        )

        response = self.client.get(reverse("plugins:netbox_ssot:run_detail", kwargs={"pk": self.run.pk}))

        assert response.status_code == 200
        self.assertContains(response, "The background worker is comparing remote observations with local NetBox.")
        self.assertContains(response, "This status updates automatically.")
        self.assertNotContains(response, "Next: Prepare comparison")

    def test_pending_status_is_polled_without_a_reload_action(self) -> None:
        ComparisonPreparation.objects.create(
            collection_run=self.run,
            state=ComparisonPreparation.State.PENDING,
            attempt_count=1,
        )

        response = self.client.get(
            reverse("plugins:netbox_ssot:run_status", kwargs={"pk": self.run.pk}),
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        self.assertContains(response, "spinner-border-sm")
        self.assertContains(response, 'hx-trigger="every 5s"')
        self.assertNotContains(response, "Refresh status")
        self.assertNotContains(response, "Check status")

    def test_completed_status_opens_the_prepared_comparison(self) -> None:
        comparison = ComparisonRun.objects.create(
            collection_run=self.run,
            source_payload_digest=self.run.payload_digest,
            target_snapshot_digest="d" * 64,
            engine_version=ENGINE_VERSION,
            no_change_count=1,
        )
        ComparisonPreparation.objects.create(
            collection_run=self.run,
            comparison=comparison,
            state=ComparisonPreparation.State.COMPLETED,
            attempt_count=1,
            completed_at=timezone.now(),
        )

        response = self.client.get(
            reverse("plugins:netbox_ssot:run_status", kwargs={"pk": self.run.pk}),
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 204
        assert response.headers["HX-Redirect"] == reverse(
            "plugins:netbox_ssot:comparison_detail",
            kwargs={"pk": comparison.pk},
        )

    def test_overview_uses_stored_aggregates_for_the_drift_chart(self) -> None:
        comparison = ComparisonRun.objects.create(
            collection_run=self.run,
            source_payload_digest=self.run.payload_digest,
            target_snapshot_digest="c" * 64,
            engine_version=ENGINE_VERSION,
            create_count=2,
            update_count=1,
            no_change_count=6,
            conflict_count=1,
        )
        ComparisonPreparation.objects.create(
            collection_run=self.run,
            comparison=comparison,
            state=ComparisonPreparation.State.COMPLETED,
            attempt_count=1,
            completed_at=timezone.now(),
        )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("plugins:netbox_ssot:overview") + "?view=summary&period=30")

        assert response.status_code == 200
        summary = response.context["drift_summary"]
        assert summary.matching == 6
        assert summary.drifted == 3
        assert summary.needs_attention == 1
        self.assertContains(response, "Estate alignment")
        self.assertContains(response, "60.0%")
        self.assertContains(response, "6 of 10 records match")
        self.assertContains(response, "Actionable drift")
        assert not any("netbox_ssot_comparisonitem" in query["sql"] for query in queries.captured_queries)

    def test_complete_ingest_requests_background_preparation(self) -> None:
        now = timezone.now()
        datasets = selected_dataset_ids(ProviderRegistry().get("netbox").manifest, ("regions",))
        batch = ObservationBatch(
            run_id=uuid4(),
            source_id=self.source.pk,
            provider_id="netbox",
            provider_version="0.0.1",
            contract_version="1.0",
            state="complete",
            started_at=now,
            completed_at=now,
            datasets=datasets,
            scope=(),
            completeness_token="complete",
        )
        request = APIRequestFactory().post(
            reverse("plugins-api:netbox_ssot-api:batch-ingest"),
            data=batch.model_dump_json(),
            content_type="application/json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True),
            token=self.agent,
        )
        queued = SimpleNamespace(preparation=SimpleNamespace(state=ComparisonPreparation.State.PENDING))

        with patch("netbox_ssot.api.views.request_comparison_preparation", return_value=queued) as prepare:
            response = BatchIngestView.as_view()(request)

        assert response.status_code == 201, response.data
        assert response.data["comparison_preparation"] == ComparisonPreparation.State.PENDING
        prepare.assert_called_once()
        assert prepare.call_args.args[0].pk == batch.run_id
