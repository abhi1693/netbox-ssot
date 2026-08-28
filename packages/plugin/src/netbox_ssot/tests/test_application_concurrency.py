from __future__ import annotations

from threading import Event, Thread
from typing import ClassVar
from unittest.mock import patch
from uuid import uuid4

from dcim.models import Region
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from netbox_ssot.application import service
from netbox_ssot.application.service import ApplicationRejectedError, apply_comparison
from netbox_ssot.models import (
    ApplyRun,
    CollectionRun,
    CollectorAgent,
    ComparisonItem,
    ComparisonRun,
    DiscoverySource,
)
from netbox_ssot.planning.comparison import ENGINE_VERSION, snapshot_digest
from netbox_ssot.planning.netbox_target import load_netbox_target_records


class ApplicationConcurrencyTests(TransactionTestCase):
    # Setting available_apps makes Django use TRUNCATE CASCADE. Development
    # NetBox installations can have third-party tables with database FKs into
    # core tables which are otherwise absent from Django's flush statement.
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def setUp(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("The concurrency guarantee is implemented with PostgreSQL serializable transactions.")
        suffix = uuid4().hex
        self.user = get_user_model().objects.create_user(username=f"concurrent-apply-{suffix}")
        self.agent = CollectorAgent.objects.create(name=f"concurrent-apply-agent-{suffix}", public_key="A" * 43)
        self.source = DiscoverySource.objects.create(
            name=f"concurrent-apply-source-{suffix}",
            provider_id="netbox",
            configuration={},
            datasets=["regions"],
            assigned_agent=self.agent,
        )
        self.region = Region.objects.create(
            name=f"Concurrent region {suffix}",
            slug=f"concurrent-region-{suffix}",
            description="reviewed baseline",
        )
        target_records = load_netbox_target_records()
        target_record = next(
            record
            for record in target_records
            if record.resource_kind == "region" and record.target_object_id == str(self.region.pk)
        )
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
            observation_count=1,
        )
        self.comparison = ComparisonRun.objects.create(
            collection_run=run,
            source_payload_digest=run.payload_digest,
            target_snapshot_digest=snapshot_digest(target_records),
            engine_version=ENGINE_VERSION,
            update_count=1,
        )
        desired_attributes = dict(target_record.attributes)
        desired_attributes["/description"] = "reviewed SSoT value"
        ComparisonItem.objects.create(
            comparison=self.comparison,
            sequence=0,
            action=ComparisonItem.Action.UPDATE,
            resource_kind="region",
            identity_key=target_record.identity_key,
            display_name=target_record.display_name,
            source_external_id="netbox:region:1",
            target_object_type=target_record.target_object_type,
            target_object_id=target_record.target_object_id,
            match_basis="natural_identity",
            source_data={"attributes": desired_attributes, "relationships": target_record.relationships},
            target_data={"attributes": target_record.attributes, "relationships": target_record.relationships},
            changes=[{"path": "/description", "before": "reviewed baseline", "after": "reviewed SSoT value"}],
        )

    def test_concurrent_netbox_edit_is_not_overwritten_by_stale_review(self) -> None:
        snapshot_checked = Event()
        continue_apply = Event()
        errors: list[Exception] = []
        original_resolver = service._resolve_external_references

        def wait_after_snapshot(records):
            snapshot_checked.set()
            if not continue_apply.wait(timeout=5):
                raise RuntimeError("Timed out waiting for concurrent target edit.")
            return original_resolver(records)

        def run_apply() -> None:
            try:
                comparison = ComparisonRun.objects.get(pk=self.comparison.pk)
                user = get_user_model().objects.get(pk=self.user.pk)
                apply_comparison(comparison, user)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        with patch.object(service, "_resolve_external_references", side_effect=wait_after_snapshot):
            worker = Thread(target=run_apply, daemon=True)
            worker.start()
            assert snapshot_checked.wait(timeout=5), "Apply did not reach its checked target snapshot."
            Region.objects.filter(pk=self.region.pk).update(description="ordinary NetBox writer")
            continue_apply.set()
            worker.join(timeout=5)

        assert not worker.is_alive(), "Apply worker did not finish."
        assert len(errors) == 1
        assert isinstance(errors[0], ApplicationRejectedError)
        assert "changed concurrently" in str(errors[0])
        self.region.refresh_from_db()
        assert self.region.description == "ordinary NetBox writer"
        assert not ApplyRun.objects.filter(comparison=self.comparison).exists()
