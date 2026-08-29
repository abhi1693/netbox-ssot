from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from dcim.models import Site
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from extras.models import CustomField

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import _resolve_external_references, _write_object
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class CustomFieldValueTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_primitive_and_object_custom_fields_round_trip_without_source_primary_keys(self) -> None:
        suffix = uuid4().hex[:8]
        site_type = ContentType.objects.get_for_model(Site)
        text_field = CustomField.objects.create(type="text", name=f"support_tier_{suffix}")
        object_field = CustomField.objects.create(
            type="object",
            name=f"failover_site_{suffix}",
            related_object_type=site_type,
        )
        multi_field = CustomField.objects.create(
            type="multiobject",
            name=f"peer_sites_{suffix}",
            related_object_type=site_type,
        )
        for custom_field in (text_field, object_field, multi_field):
            custom_field.object_types.set([site_type])

        primary = Site.objects.create(name=f"Primary {suffix}", slug=f"primary-{suffix}")
        failover = Site.objects.create(name=f"Failover {suffix}", slug=f"failover-{suffix}")
        peer = Site.objects.create(name=f"Peer {suffix}", slug=f"peer-{suffix}")
        primary.custom_field_data = {
            text_field.name: "gold",
            object_field.name: failover.pk,
            multi_field.name: [failover.pk, peer.pk],
        }
        primary.full_clean()
        primary.save()

        selected = {
            ("custom_field", str(text_field.pk)),
            ("custom_field", str(object_field.pk)),
            ("custom_field", str(multi_field.pk)),
            ("site", str(primary.pk)),
            ("site", str(failover.pk)),
            ("site", str(peer.pk)),
        }
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.resource_kind, record.target_object_id) in selected
        ]
        primary_record = next(
            record
            for record in canonical
            if record.resource_kind == "site" and record.target_object_id == str(primary.pk)
        )
        assert primary_record.attributes["/custom_fields"] == {
            text_field.name: "gold",
            object_field.name: None,
            multi_field.name: [],
        }
        object_relationship = f"custom_field_object_site_{object_field.name}"
        multi_relationship = f"custom_field_multi_site_{multi_field.name}"
        assert set(primary_record.relationships) >= {object_relationship, multi_relationship}
        assert "netbox:" not in str(primary_record.relationships[object_relationship])
        assert "netbox:" not in str(primary_record.relationships[multi_relationship])

        expected = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        application_records = [
            ApplicationRecord(record.resource_kind, record.identity_key, record.attributes, record.relationships)
            for record in canonical
        ]

        primary.delete()
        failover.delete()
        peer.delete()
        text_field.delete()
        object_field.delete()
        multi_field.delete()

        target_records = load_netbox_target_records()
        target_by_key = {(record.resource_kind, record.identity_key): record for record in target_records}
        references, problems = _resolve_external_references(application_records)
        assert problems == ()
        object_cache: dict[tuple[str, str], object] = {}
        for record in dependency_order(application_records):
            obj = MODEL_BY_KIND[record.resource_kind]()
            _write_object(obj, record, target_by_key, object_cache, references)
            object_cache[record.key] = obj

        recreated = {
            (record.resource_kind, record.identity_key): record.payload
            for record in load_netbox_target_records()
            if (record.resource_kind, record.identity_key) in expected
        }
        assert recreated == expected
