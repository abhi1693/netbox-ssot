from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from core.models import ObjectType
from dcim.models import Region
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from users.models import ObjectPermission

from netbox_ssot.application.planning import ReferenceRequirement
from netbox_ssot.application.service import (
    _records_by_item,
    _reference_problem_message,
    apply_comparison,
    inspect_application,
)
from netbox_ssot.models import (
    CollectionRun,
    CollectorAgent,
    ComparisonItem,
    ComparisonReview,
    ComparisonRun,
    DiscoverySource,
    ReviewDecision,
    StoredObservation,
    SynchronizationDirection,
)
from netbox_ssot.planning.comparison import ENGINE_VERSION, snapshot_digest
from netbox_ssot.planning.netbox_target import load_netbox_target_records
from netbox_ssot.review import (
    ReviewRejectedError,
    approve_all_review_items,
    finalize_review,
    latest_review_decision,
    latest_review_decisions,
    record_review_decision,
    review_decision_digest,
    review_integrity_issue,
    review_progress,
)

FOUR_EYES_CONFIG = {"netbox_ssot": {"require_separate_reviewer_and_applier": True}}


class ReviewWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        suffix = uuid4().hex
        cls.reviewer = get_user_model().objects.create_user(username=f"reviewer-{suffix}")
        cls.applier = get_user_model().objects.create_user(username=f"applier-{suffix}")
        cls.agent = CollectorAgent.objects.create(name=f"review-agent-{suffix}", public_key="A" * 43)
        cls.source = DiscoverySource.objects.create(
            name=f"review-source-{suffix}",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
            assigned_agent=cls.agent,
        )

    def setUp(self) -> None:
        run = CollectionRun.objects.create(
            run_id=uuid4(),
            source=self.source,
            agent=self.agent,
            provider_id="netbox",
            provider_version="0.0.4",
            contract_version="1.0",
            state="complete",
            started_at=timezone.now(),
            completed_at=timezone.now(),
            datasets=["regions"],
            scope=[],
            messages=[],
            completeness_token="complete",
            payload_digest="a" * 64,
            observation_count=2,
        )
        self.comparison = ComparisonRun.objects.create(
            collection_run=run,
            source_payload_digest=run.payload_digest,
            target_snapshot_digest=snapshot_digest(load_netbox_target_records()),
            engine_version=ENGINE_VERSION,
            create_count=2,
        )
        self.items = tuple(
            ComparisonItem.objects.create(
                comparison=self.comparison,
                sequence=sequence,
                action=ComparisonItem.Action.CREATE,
                resource_kind="region",
                identity_key=f'["region","region-{sequence}"]',
                display_name=f"Region {sequence}",
                source_external_id=f"netbox:region:{sequence}",
                source_data={
                    "attributes": {
                        "/name": f"Region {sequence}",
                        "/slug": f"region-{sequence}",
                        "/description": "",
                    },
                    "relationships": {},
                },
                target_data={},
                changes=[],
            )
            for sequence in range(2)
        )
        StoredObservation.objects.bulk_create(
            [
                StoredObservation(
                    run=run,
                    source=self.source,
                    sequence=sequence,
                    resource_kind="region",
                    external_id=f"netbox:region:{sequence}",
                    collected_at=timezone.now(),
                    scope=[],
                    attributes=[],
                    relationships=[],
                    evidence=[],
                    fingerprint=str(sequence) * 64,
                )
                for sequence in range(2)
            ]
        )

    def test_record_decisions_are_append_only_and_latest_wins(self) -> None:
        with self.assertRaisesMessage(ReviewRejectedError, "Explain why"):
            record_review_decision(
                self.comparison,
                self.items[0],
                ReviewDecision.Decision.REJECT,
                self.reviewer,
            )

        rejected = record_review_decision(
            self.comparison,
            self.items[0],
            ReviewDecision.Decision.REJECT,
            self.reviewer,
            reason="Wrong source value.",
        )
        approved = record_review_decision(
            self.comparison,
            self.items[0],
            ReviewDecision.Decision.APPROVE,
            self.reviewer,
        )

        assert ReviewDecision.objects.filter(comparison=self.comparison).count() == 2
        assert latest_review_decisions(self.comparison)[self.items[0].pk] == approved
        assert latest_review_decisions(self.comparison, item_ids=(self.items[0].pk,)) == {self.items[0].pk: approved}
        assert latest_review_decision(self.items[0]) == approved
        with pytest.raises(ValidationError):
            rejected.save()

    def test_approval_requires_every_actionable_item_and_then_locks_review(self) -> None:
        record_review_decision(
            self.comparison,
            self.items[0],
            ReviewDecision.Decision.APPROVE,
            self.reviewer,
        )
        with self.assertRaisesMessage(ReviewRejectedError, "1 undecided"):
            finalize_review(
                self.comparison,
                ComparisonReview.Decision.APPROVED,
                self.reviewer,
            )

        progress = approve_all_review_items(self.comparison, self.reviewer)
        assert progress.approved_count == 2
        assert progress.undecided_count == 0
        review = finalize_review(
            self.comparison,
            ComparisonReview.Decision.APPROVED,
            self.reviewer,
        )

        assert review.approved_count == 2
        assert review.rejected_count == 0
        assert review_integrity_issue(review) == ""
        with pytest.raises(ValidationError):
            review.save()
        with self.assertRaisesMessage(ReviewRejectedError, "finalized"):
            record_review_decision(
                self.comparison,
                self.items[0],
                ReviewDecision.Decision.REJECT,
                self.reviewer,
                reason="Too late.",
            )

    def test_rejection_is_final_and_requires_a_reason(self) -> None:
        with self.assertRaisesMessage(ReviewRejectedError, "Explain why"):
            finalize_review(
                self.comparison,
                ComparisonReview.Decision.REJECTED,
                self.reviewer,
            )

        review = finalize_review(
            self.comparison,
            ComparisonReview.Decision.REJECTED,
            self.reviewer,
            reason="The source needs correction.",
        )

        assert review.decision == ComparisonReview.Decision.REJECTED
        assert "rejected" in " ".join(inspect_application(self.comparison).reasons).lower()

    def test_final_review_digest_detects_decision_tampering(self) -> None:
        approve_all_review_items(self.comparison, self.reviewer)
        review = finalize_review(
            self.comparison,
            ComparisonReview.Decision.APPROVED,
            self.reviewer,
        )
        latest = latest_review_decisions(self.comparison)[self.items[0].pk]

        ReviewDecision.objects.filter(pk=latest.pk).update(reason="tampered")
        review.refresh_from_db()

        assert "no longer matches" in review_integrity_issue(review)
        assert "no longer matches" in " ".join(inspect_application(self.comparison).reasons)

    def test_final_review_digest_covers_synchronization_direction(self) -> None:
        approve_all_review_items(self.comparison, self.reviewer)
        review = finalize_review(
            self.comparison,
            ComparisonReview.Decision.APPROVED,
            self.reviewer,
        )

        ComparisonRun.objects.filter(pk=self.comparison.pk).update(
            direction=SynchronizationDirection.TARGET_TO_SOURCE
        )
        self.comparison.refresh_from_db()
        review.refresh_from_db()

        assert "no longer matches" in review_integrity_issue(review)

    def test_apply_readiness_requires_a_finalized_approval(self) -> None:
        pending = inspect_application(self.comparison, self.applier)
        assert "finalized review" in " ".join(pending.reasons)

        approve_all_review_items(self.comparison, self.reviewer)
        finalize_review(
            self.comparison,
            ComparisonReview.Decision.APPROVED,
            self.reviewer,
        )

        readiness = inspect_application(self.comparison, self.applier)
        assert readiness.ready, (
            readiness.reasons,
            readiness.current_target_digest,
            self.comparison.target_snapshot_digest,
        )

    def test_approved_comparison_executes_and_records_its_direction(self) -> None:
        approve_all_review_items(self.comparison, self.reviewer)
        finalize_review(
            self.comparison,
            ComparisonReview.Decision.APPROVED,
            self.reviewer,
        )

        # TestCase owns an outer transaction; production apply starts its own
        # serializable transaction, which is covered by TransactionTestCase.
        with patch("netbox_ssot.application.service._set_apply_transaction_isolation"):
            outcome = apply_comparison(self.comparison, self.applier)

        assert outcome.created
        assert outcome.apply_run.direction == SynchronizationDirection.SOURCE_TO_TARGET
        assert set(Region.objects.filter(slug__startswith="region-").values_list("slug", flat=True)) == {
            "region-0",
            "region-1",
        }
        assert outcome.apply_run.items.count() == 2

    def test_reverse_direction_fails_closed_until_the_provider_can_write(self) -> None:
        ComparisonRun.objects.filter(pk=self.comparison.pk).update(
            direction=SynchronizationDirection.TARGET_TO_SOURCE
        )
        self.comparison.refresh_from_db()

        readiness = inspect_application(self.comparison, self.applier)

        assert not readiness.ready
        assert "remote write capability" in " ".join(readiness.reasons)

    def test_external_reference_message_has_an_exact_non_negative_remainder(self) -> None:
        problems = (
            (ReferenceRequirement("users.user", "username", "alice"), 0),
            (ReferenceRequirement("users.user", "username", "bob"), 0),
        )

        message = _reference_problem_message(problems)

        assert "Create or uniquely match 2 local objects" in message
        assert "User with username 'alice' (missing)" in message
        assert "User with username 'bob' (missing)" in message
        assert "plus" not in message
        assert "-8" not in message

    def test_forged_approval_without_item_decisions_fails_closed(self) -> None:
        ComparisonReview.objects.create(
            comparison=self.comparison,
            decision=ComparisonReview.Decision.APPROVED,
            reviewed_by=self.reviewer,
            decision_digest=review_decision_digest(
                self.comparison,
                final_decision=ComparisonReview.Decision.APPROVED,
                reviewed_by_id=str(self.reviewer.pk),
                reason="",
            ),
        )

        readiness = inspect_application(self.comparison, self.applier)

        assert not readiness.ready
        assert "does not approve every actionable" in " ".join(readiness.reasons)

    @override_settings(PLUGINS_CONFIG=FOUR_EYES_CONFIG)
    def test_optional_four_eyes_policy_blocks_reviewer_from_applying(self) -> None:
        approve_all_review_items(self.comparison, self.reviewer)
        finalize_review(
            self.comparison,
            ComparisonReview.Decision.APPROVED,
            self.reviewer,
        )

        same_operator = inspect_application(self.comparison, self.reviewer)
        different_operator = inspect_application(self.comparison, self.applier)

        assert "different operator" in " ".join(same_operator.reasons)
        assert different_operator.ready, different_operator.reasons

    def test_review_permission_controls_decision_endpoint(self) -> None:
        url = reverse(
            "plugins:netbox_ssot:comparison_item_decide",
            kwargs={"comparison_pk": self.comparison.pk, "pk": self.items[0].pk},
        )
        self.client.force_login(self.reviewer)

        denied = self.client.post(url, {"decision": ReviewDecision.Decision.APPROVE})
        assert denied.status_code == 403

        permission = ObjectPermission.objects.create(name="Review comparisons", actions=["add"])
        permission.users.add(self.reviewer)
        permission.object_types.add(ObjectType.objects.get_for_model(ComparisonReview))
        self.reviewer = get_user_model().objects.get(pk=self.reviewer.pk)
        self.client.force_login(self.reviewer)
        allowed = self.client.post(url, {"decision": ReviewDecision.Decision.APPROVE})

        assert allowed.status_code == 302
        assert review_progress(self.comparison).approved_count == 1

    def test_review_page_renders_lifecycle_and_actions(self) -> None:
        self.reviewer.is_superuser = True
        self.reviewer.save(update_fields=("is_superuser",))
        self.client.force_login(self.reviewer)

        with patch("netbox_ssot.views.latest_review_decisions", wraps=latest_review_decisions) as latest:
            response = self.client.get(
                reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": self.comparison.pk})
            )

        assert response.status_code == 200
        assert latest.call_args.kwargs["item_ids"] == tuple(item.pk for item in self.items)
        self.assertContains(response, "Review decisions")
        self.assertContains(response, "Approve all and finalize")

    def test_item_detail_fetches_only_its_latest_decision(self) -> None:
        self.reviewer.is_superuser = True
        self.reviewer.save(update_fields=("is_superuser",))
        self.client.force_login(self.reviewer)
        record_review_decision(
            self.comparison,
            self.items[0],
            ReviewDecision.Decision.APPROVE,
            self.reviewer,
        )

        with patch(
            "netbox_ssot.views.latest_review_decisions",
            side_effect=AssertionError("item detail must not build the comparison-wide decision map"),
        ):
            response = self.client.get(
                reverse(
                    "plugins:netbox_ssot:comparison_item_detail",
                    kwargs={"comparison_pk": self.comparison.pk, "pk": self.items[0].pk},
                )
            )

        assert response.status_code == 200
        self.assertContains(response, "Approve")

    def test_item_detail_uses_related_record_labels(self) -> None:
        self.reviewer.is_superuser = True
        self.reviewer.save(update_fields=("is_superuser",))
        self.client.force_login(self.reviewer)
        self.items[0].source_data = {
            **self.items[0].source_data,
            "relationships": {"parent": self.items[1].identity_key},
        }
        ComparisonItem.objects.filter(pk=self.items[0].pk).update(source_data=self.items[0].source_data)

        response = self.client.get(
            reverse(
                "plugins:netbox_ssot:comparison_item_detail",
                kwargs={"comparison_pk": self.comparison.pk, "pk": self.items[0].pk},
            )
        )

        assert response.status_code == 200
        parent_row = next(row for row in response.context["field_rows"] if row.field == "parent")
        assert parent_row.provider_value == "Region · Region 1"

    def test_legacy_rack_reservation_user_is_normalized_to_a_relationship(self) -> None:
        item = self.items[0]
        item.resource_kind = "rack_reservation"
        item.display_name = "Rack reservation 1"
        item.source_data = {
            "attributes": {"/units": [20, 21], "/user": "bob"},
            "relationships": {"rack": '["rack","site","R101"]'},
        }

        record = _records_by_item([item])[item.pk]

        assert record.attributes == {"/units": [20, 21]}
        assert record.relationships == {
            "rack": '["rack","site","R101"]',
            "user": '["user","username","bob"]',
        }
        assert record.display_name == "Rack reservation 1"

    def test_review_page_explains_why_apply_is_unavailable(self) -> None:
        self.comparison.skipped_count = 1
        ComparisonRun.objects.filter(pk=self.comparison.pk).update(skipped_count=1)
        ComparisonItem.objects.create(
            comparison=self.comparison,
            sequence=3,
            action=ComparisonItem.Action.SKIPPED,
            resource_kind="device",
            identity_key="unresolved-device",
            display_name="Unnamed device",
            source_external_id="netbox:device:blocked",
            match_basis="unresolved_identity",
            reason="A portable identity could not be established.",
        )
        self.reviewer.is_superuser = True
        self.reviewer.save(update_fields=("is_superuser",))
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("plugins:netbox_ssot:comparison_detail", kwargs={"pk": self.comparison.pk}))

        self.assertContains(response, "Apply unavailable")
        self.assertContains(response, "Why apply is unavailable")
        self.assertContains(response, "A portable identity could not be established.")
        self.assertContains(response, "?action=skipped")
        self.assertContains(response, "apply remains blocked")

    def test_comparison_list_marks_no_change_comparison_as_resolved(self) -> None:
        ComparisonRun.objects.create(
            collection_run=self.comparison.collection_run,
            source_payload_digest=self.comparison.source_payload_digest,
            target_snapshot_digest="f" * 64,
            engine_version=self.comparison.engine_version,
            no_change_count=1,
        )
        self.reviewer.is_superuser = True
        self.reviewer.save(update_fields=("is_superuser",))
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("plugins:netbox_ssot:comparison_list"))

        assert response.status_code == 200
        self.assertContains(response, "No changes", count=1)
        self.assertContains(response, "In review", count=1)
