from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from django.apps import apps
from django.test import TestCase
from extras.models import Tag
from tenancy.models import Contact, ContactAssignment, ContactGroup, ContactRole, Tenant, TenantGroup
from users.models import Owner

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import _resolve_external_references, _write_object
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class TenancyCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_every_public_tenancy_resource_round_trips_through_snapshot_and_writer(self) -> None:
        suffix = uuid4().hex[:8]
        owner = Owner.objects.create(name=f"Tenancy owner {suffix}")
        tag = Tag.objects.create(name=f"Tenancy {suffix}", slug=f"tenancy-{suffix}")
        tenant_group = TenantGroup.objects.create(name=f"Customers {suffix}", slug=f"customers-{suffix}")
        tenant = Tenant.objects.create(
            name=f"Customer {suffix}", slug=f"customer-{suffix}", group=tenant_group, owner=owner
        )
        parent_group = ContactGroup.objects.create(
            name=f"Operations {suffix}", slug=f"operations-{suffix}", owner=owner
        )
        child_group = ContactGroup.objects.create(
            name=f"Escalations {suffix}", slug=f"escalations-{suffix}", parent=parent_group, owner=owner
        )
        role = ContactRole.objects.create(
            name=f"Technical {suffix}", slug=f"technical-{suffix}", owner=owner
        )
        contact = Contact.objects.create(
            name=f"Alice {suffix}",
            title="Network engineer",
            phone="+1-555-0100",
            email=f"alice-{suffix}@example.com",
            address="1 Example Way",
            link="https://example.com/contact",
            owner=owner,
        )
        contact.groups.set([child_group])
        assignment = ContactAssignment(object=tenant, contact=contact, role=role, priority="primary")
        assignment.full_clean()
        assignment.save()

        objects = (tenant_group, tenant, parent_group, child_group, role, contact, assignment)
        for obj in objects:
            obj.tags.set([tag])

        source_pks = {(obj._meta.label_lower, str(obj.pk)) for obj in objects}
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.target_object_type, record.target_object_id) in source_pks
        ]
        assert len(canonical) == len(objects)
        assignment_record = next(record for record in canonical if record.resource_kind == "contact_assignment")
        assert assignment_record.attributes["/object_type"] == "tenancy.tenant"
        assert "object_tenant" in assignment_record.relationships

        expected = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        application_records = [
            ApplicationRecord(record.resource_kind, record.identity_key, record.attributes, record.relationships)
            for record in canonical
        ]

        for obj in reversed(objects):
            obj.delete()

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

        recreated_assignment = object_cache[assignment_record.resource_kind, assignment_record.identity_key]
        assert recreated_assignment.object == object_cache[
            next(record.key for record in application_records if record.resource_kind == "tenant")
        ]
        assert recreated_assignment.contact.groups.get().slug == f"escalations-{suffix}"
