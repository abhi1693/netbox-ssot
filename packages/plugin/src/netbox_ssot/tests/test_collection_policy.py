from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from netbox_ssot.api.views import _agent_assignment
from netbox_ssot.collection_policy import (
    agent_collection_policy_issue,
    source_collection_schedule_policy,
)
from netbox_ssot.models import (
    ApplyRun,
    CollectionRun,
    CollectorAgent,
    ComparisonReview,
    ComparisonRun,
    DiscoverySource,
)

PAUSE_CONFIG = {"netbox_ssot": {"pause_scheduled_collections_until_resolved": True}}


class CollectionPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create_user(username="collection-policy-user")

    def setUp(self) -> None:
        self.agent = CollectorAgent.objects.create(
            name=f"policy-agent-{uuid4()}",
            public_key="A" * 43,
            agent_version="0.0.1",
        )
        self.source = DiscoverySource.objects.create(
            name=f"policy-source-{uuid4()}",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
            assigned_agent=self.agent,
        )

    def _run(self, *, minutes_ago: int = 0) -> CollectionRun:
        completed_at = timezone.now() - timedelta(minutes=minutes_ago)
        run = CollectionRun.objects.create(
            run_id=uuid4(),
            source=self.source,
            agent=self.agent,
            provider_id="netbox",
            provider_version="0.0.4",
            contract_version="1.0",
            state="complete",
            started_at=completed_at - timedelta(seconds=1),
            completed_at=completed_at,
            datasets=["regions"],
            scope=[],
            messages=[],
            completeness_token="complete",
            payload_digest=uuid4().hex + uuid4().hex,
            observation_count=1,
        )
        CollectionRun.objects.filter(pk=run.pk).update(received_at=completed_at)
        run.refresh_from_db()
        return run

    @override_settings(PLUGINS_CONFIG=PAUSE_CONFIG)
    def test_complete_collection_pauses_until_review_is_resolved(self) -> None:
        run = self._run()

        waiting = source_collection_schedule_policy(self.source)

        assert not waiting.enabled
        assert str(run.run_id) in waiting.reason
        assignment = _agent_assignment(self.source)
        assert not assignment.schedule_enabled
        assert assignment.schedule_pause_reason == waiting.reason

        comparison = ComparisonRun.objects.create(
            collection_run=run,
            source_payload_digest=run.payload_digest,
            target_snapshot_digest="a" * 64,
            engine_version="test",
            no_change_count=1,
        )

        assert source_collection_schedule_policy(self.source).enabled

        comparison.no_change_count = 0
        comparison.create_count = 1
        ComparisonRun.objects.filter(pk=comparison.pk).update(no_change_count=0, create_count=1)
        assert not source_collection_schedule_policy(self.source).enabled

        ComparisonReview.objects.create(
            comparison=comparison,
            decision=ComparisonReview.Decision.REJECTED,
            reviewed_by=self.user,
            reason="Source data needs correction.",
            decision_digest="b" * 64,
            rejected_count=1,
        )
        assert source_collection_schedule_policy(self.source).enabled

        run = self._run()
        comparison = ComparisonRun.objects.create(
            collection_run=run,
            source_payload_digest=run.payload_digest,
            target_snapshot_digest="c" * 64,
            engine_version="test",
            create_count=1,
        )
        ComparisonReview.objects.create(
            comparison=comparison,
            decision=ComparisonReview.Decision.APPROVED,
            reviewed_by=self.user,
            decision_digest="d" * 64,
            approved_count=1,
        )
        approved = source_collection_schedule_policy(self.source)
        assert not approved.enabled
        assert "approved and waiting for apply" in approved.reason

        ApplyRun.objects.create(
            comparison=comparison,
            applied_by=self.user,
            create_count=1,
        )
        assert source_collection_schedule_policy(self.source).enabled

    def test_policy_is_disabled_by_default(self) -> None:
        self._run()

        assert source_collection_schedule_policy(self.source).enabled

    @override_settings(PLUGINS_CONFIG=PAUSE_CONFIG)
    def test_enabled_policy_requires_current_agent(self) -> None:
        self.agent.agent_version = "0.0.0"

        assert "0.0.1" in agent_collection_policy_issue(self.agent)
