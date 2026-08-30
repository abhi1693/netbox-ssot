from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from dcim.models import Location, Site
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from users.models import Group, ObjectPermission, Owner, OwnerGroup

from netbox_ssot.application.planning import ApplicationRecord
from netbox_ssot.application.service import (
    _resolve_external_references,
    _sync_items,
    _write_object,
)
from netbox_ssot.models import ComparisonItem
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records
from netbox_ssot.planning.users import USERS_RESOURCE_KINDS


class UsersCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_portable_users_resources_round_trip_without_credentials(self) -> None:
        suffix = uuid4().hex[:8]
        permission = ObjectPermission.objects.create(
            name=f"Sites {suffix}",
            description="Manage active sites",
            enabled=True,
            actions=["view", "change"],
            constraints={"status": "active"},
        )
        permission.object_types.set(
            [
                ContentType.objects.get_for_model(Site),
                ContentType.objects.get_for_model(Location),
            ]
        )
        group = Group.objects.create(name=f"Operators {suffix}", description="Network operators")
        group.object_permissions.add(permission)
        user = get_user_model().objects.create_user(
            username=f"alice-{suffix}",
            password="source-only-password",
            first_name="Alice",
            last_name="Operator",
            email=f"alice-{suffix}@example.com",
            is_active=True,
            is_superuser=True,
        )
        user.groups.add(group)
        user.object_permissions.add(permission)
        standalone_user = get_user_model().objects.create_user(username=f"a-standalone-{suffix}")
        owner_group = OwnerGroup.objects.create(name=f"Infrastructure {suffix}", description="Managed owners")
        owner = Owner.objects.create(
            name=f"Network automation {suffix}",
            description="Portable owner",
            group=owner_group,
        )
        owner.user_groups.add(group)
        owner.users.add(user)

        selected = {
            ("object_permission", str(permission.pk)),
            ("user_group", str(group.pk)),
            ("user", str(user.pk)),
            ("user", str(standalone_user.pk)),
            ("owner_group", str(owner_group.pk)),
            ("owner", str(owner.pk)),
        }
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.resource_kind, record.target_object_id) in selected
        ]

        assert {record.resource_kind for record in canonical} == USERS_RESOURCE_KINDS
        user_record = next(
            record
            for record in canonical
            if record.resource_kind == "user" and record.target_object_id == str(user.pk)
        )
        assert set(user_record.attributes) == {"/username", "/first_name", "/last_name", "/email", "/is_active"}
        assert user_record.relationships == {
            "group": [next(record.identity_key for record in canonical if record.resource_kind == "user_group")],
            "permission": [
                next(record.identity_key for record in canonical if record.resource_kind == "object_permission")
            ],
        }
        assert all(
            forbidden not in user_record.attributes
            for forbidden in {"/password", "/is_superuser", "/date_joined", "/last_login"}
        )
        owner_record = next(record for record in canonical if record.resource_kind == "owner")
        assert owner_record.relationships == {
            "group": next(record.identity_key for record in canonical if record.resource_kind == "owner_group"),
            "user_group": [
                next(record.identity_key for record in canonical if record.resource_kind == "user_group")
            ],
            "user": [user_record.identity_key],
        }

        expected = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        records = {
            sequence: ApplicationRecord(
                record.resource_kind,
                record.identity_key,
                record.attributes,
                record.relationships,
                record.display_name,
            )
            for sequence, record in enumerate(canonical, start=1)
        }
        items = [
            ComparisonItem(
                pk=sequence,
                sequence=sequence,
                action=ComparisonItem.Action.CREATE,
                resource_kind=record.resource_kind,
                identity_key=record.identity_key,
                display_name=record.display_name,
                source_external_id=record.external_id,
                source_data=record.payload,
            )
            for sequence, record in enumerate(canonical, start=1)
        ]

        owner.delete()
        owner_group.delete()
        user.delete()
        standalone_user.delete()
        group.delete()
        permission.delete()

        target_records = load_netbox_target_records()
        references, problems = _resolve_external_references(list(records.values()))
        assert problems == ()
        _sync_items(items, records, target_records, references)

        recreated = {
            (record.resource_kind, record.identity_key): record.payload
            for record in load_netbox_target_records()
            if (record.resource_kind, record.identity_key) in expected
        }
        assert recreated == expected
        recreated_user = get_user_model().objects.get(username=f"alice-{suffix}")
        assert not recreated_user.has_usable_password()
        assert not recreated_user.is_superuser

        recreated_user.set_password("destination-only-password")
        recreated_user.is_superuser = True
        recreated_user.save()
        user_application = next(
            record for record in records.values() if record.identity_key == user_record.identity_key
        )
        target_records = load_netbox_target_records()
        target_by_key = {(record.resource_kind, record.identity_key): record for record in target_records}
        _write_object(recreated_user, user_application, target_by_key, {}, references)
        recreated_user.refresh_from_db()
        assert recreated_user.check_password("destination-only-password")
        assert recreated_user.is_superuser

    def test_grandfathered_username_is_preserved_without_sanitizing(self) -> None:
        suffix = uuid4().hex[:8]
        username = f"legacy-{suffix}#"
        user_model = get_user_model()
        legacy_user = user_model.objects.create(username=username, is_active=True)
        source = next(
            record
            for record in load_netbox_target_records(datasets=("users",))
            if record.resource_kind == "user" and record.target_object_id == str(legacy_user.pk)
        )
        legacy_user.delete()

        record = ApplicationRecord(
            source.resource_kind,
            source.identity_key,
            source.attributes,
            source.relationships,
            source.display_name,
        )
        recreated = MODEL_BY_KIND[record.resource_kind]()
        _write_object(recreated, record, {}, {}, {})

        recreated.refresh_from_db()
        assert recreated.username == username
        assert not recreated.has_usable_password()
