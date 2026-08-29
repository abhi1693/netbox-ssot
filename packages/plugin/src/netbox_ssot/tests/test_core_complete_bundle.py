from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from core.models import DataSource
from django.apps import apps
from django.test import TestCase
from users.models import Owner

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import (
    _resolve_external_references,
    _write_deferred_relationships,
    _write_object,
)
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class CoreCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_data_source_round_trip_preserves_destination_credentials_and_runtime_state(self) -> None:
        suffix = uuid4().hex[:8]
        owner = Owner.objects.create(name=f"Automation {suffix}")
        source = DataSource.objects.create(
            name=f"Automation {suffix}",
            type="git",
            source_url=f"https://git.example.com/network/{suffix}.git",
            enabled=True,
            sync_interval=60,
            ignore_rules="secrets/*\n*.tmp",
            parameters={
                "branch": "production",
                "username": "source-user",
                "password": "source-password",
            },
            owner=owner,
            description="Network automation",
            comments="Managed from the source",
        )

        canonical = next(
            record
            for record in load_netbox_target_records()
            if record.resource_kind == "data_source" and record.target_object_id == str(source.pk)
        )
        assert canonical.attributes == {
            "/name": f"Automation {suffix}",
            "/type": "git",
            "/source_url": f"https://git.example.com/network/{suffix}.git",
            "/enabled": True,
            "/sync_interval": 60,
            "/ignore_rules": "secrets/*\n*.tmp",
            "/description": "Network automation",
            "/comments": "Managed from the source",
            "/parameters": {"branch": "production"},
            "/custom_fields": {},
        }
        assert canonical.relationships
        assert set(canonical.relationships) == {"owner"}
        assert "source-user" not in str(canonical.payload)
        assert "source-password" not in str(canonical.payload)

        record = ApplicationRecord(
            canonical.resource_kind,
            canonical.identity_key,
            canonical.attributes,
            canonical.relationships,
        )
        source.delete()

        target_records = load_netbox_target_records()
        target_by_key = {(item.resource_kind, item.identity_key): item for item in target_records}
        references, problems = _resolve_external_references([record])
        assert problems == ()
        object_cache: dict[tuple[str, str], object] = {}
        ordered = dependency_order([record])
        recreated = MODEL_BY_KIND[record.resource_kind]()
        _write_object(recreated, record, target_by_key, object_cache, references)
        object_cache[record.key] = recreated
        _write_deferred_relationships(ordered, target_by_key, object_cache)

        recreated.refresh_from_db()
        assert recreated.parameters == {"branch": "production"}
        # NetBox schedules the configured recurring sync on create, producing
        # destination-owned runtime state rather than copying the source status.
        assert recreated.status == "queued"
        assert recreated.last_synced is None
        assert recreated.owner == owner

        recreated.parameters = {
            "branch": "destination-stale",
            "username": "destination-user",
            "password": "destination-password",
            "plugin_local": "keep",
        }
        recreated.status = "completed"
        recreated.save()
        _write_object(recreated, record, target_by_key, object_cache, references)
        recreated.refresh_from_db()

        assert recreated.parameters == {
            "branch": "production",
            "username": "destination-user",
            "password": "destination-password",
            "plugin_local": "keep",
        }
        assert recreated.status == "completed"
