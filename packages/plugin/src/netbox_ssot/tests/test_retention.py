from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from netbox_ssot.models import (
    ApplyRun,
    CollectionRun,
    CollectorAgent,
    ComparisonRun,
    DiscoverySource,
    StoredObservation,
)
from netbox_ssot.retention import prune_collections, retention_plan


class RetentionTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.agent = CollectorAgent.objects.create(
            name="retention-agent",
            public_key="A" * 43,
        )
        cls.user = get_user_model().objects.create_user(username="retention-user")

    def setUp(self) -> None:
        self.now = timezone.now()
        self.source = DiscoverySource.objects.create(
            name=f"retention-source-{uuid4()}",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
            assigned_agent=self.agent,
            retention_days=30,
            retention_successful_runs=2,
            retention_failure_days=7,
        )

    def test_pruning_obeys_count_age_and_review_protection(self) -> None:
        newest_success = self._run(state="complete", age=timedelta(hours=1))
        retained_by_count = self._run(state="complete", age=timedelta(hours=2))
        outside_count = self._run(state="complete", age=timedelta(hours=3))
        reviewed = self._run(state="complete", age=timedelta(days=40), reviewed=True)
        applied = self._run(state="complete", age=timedelta(days=50), reviewed=True, applied=True)
        outside_age = self._run(state="complete", age=timedelta(days=60))
        expired_failure = self._run(state="failed", age=timedelta(days=10))
        latest_partial = self._run(state="partial", age=timedelta(minutes=30))

        plan = retention_plan(self.source, now=self.now)

        assert set(plan.eligible_run_ids) == {
            outside_count.run_id,
            outside_age.run_id,
            expired_failure.run_id,
        }
        assert plan.eligible_observations == 2
        assert plan.review_protected_runs == 2

        result = prune_collections(self.source, apply=True, now=self.now)

        assert result.deleted_runs == 3
        assert result.deleted_observations == 2
        assert set(CollectionRun.objects.filter(source=self.source).values_list("run_id", flat=True)) == {
            newest_success.run_id,
            retained_by_count.run_id,
            reviewed.run_id,
            applied.run_id,
            latest_partial.run_id,
        }
        assert StoredObservation.objects.filter(run=reviewed).exists()
        assert StoredObservation.objects.filter(run=applied).exists()

    def test_newest_run_and_newest_success_are_always_retained(self) -> None:
        self.source.retention_days = 1
        self.source.retention_failure_days = 1
        self.source.retention_successful_runs = 1
        self.source.save()
        newest_success = self._run(state="complete", age=timedelta(days=100))
        newest_run = self._run(state="failed", age=timedelta(days=90))

        plan = retention_plan(self.source, now=self.now)

        assert plan.eligible_run_ids == ()
        result = prune_collections(self.source, apply=True, now=self.now)
        assert result.deleted_runs == 0
        assert CollectionRun.objects.filter(pk=newest_success.pk).exists()
        assert CollectionRun.objects.filter(pk=newest_run.pk).exists()

    def _run(
        self,
        *,
        state: str,
        age: timedelta,
        reviewed: bool = False,
        applied: bool = False,
    ) -> CollectionRun:
        completed_at = self.now - age
        run = CollectionRun.objects.create(
            run_id=uuid4(),
            source=self.source,
            agent=self.agent,
            provider_id="netbox",
            provider_version="0.0.1",
            contract_version="1.0",
            state=state,
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            datasets=["regions"],
            scope=[],
            messages=[],
            completeness_token="complete" if state == "complete" else "",
            payload_digest=uuid4().hex + uuid4().hex,
            observation_count=0 if state == "failed" else 1,
        )
        CollectionRun.objects.filter(pk=run.pk).update(received_at=completed_at)
        run.refresh_from_db()
        if state != "failed":
            StoredObservation.objects.create(
                run=run,
                source=self.source,
                sequence=1,
                resource_kind="region",
                external_id=f"netbox:region:{run.pk}",
                collected_at=completed_at,
                scope=[],
                attributes=[],
                relationships=[],
                evidence=[],
                fingerprint=uuid4().hex + uuid4().hex,
            )
        if reviewed:
            comparison = ComparisonRun.objects.create(
                collection_run=run,
                source_payload_digest=uuid4().hex + uuid4().hex,
                target_snapshot_digest=uuid4().hex + uuid4().hex,
                engine_version="test",
            )
            if applied:
                ApplyRun.objects.create(comparison=comparison, applied_by=self.user)
        return run
