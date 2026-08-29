from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from dcim.models import Site
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from extras.models import (
    ConfigContext,
    ConfigContextProfile,
    ConfigTemplate,
    CustomField,
    CustomFieldChoiceSet,
    CustomLink,
    EventRule,
    ExportTemplate,
    NotificationGroup,
    SavedFilter,
    TableConfig,
    Tag,
    Webhook,
)
from users.models import Group, Owner, User

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import _extras_problems, _resolve_external_references, _write_object
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class ExtrasCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_portable_extras_graph_round_trips_through_target_and_writer(self) -> None:
        suffix = uuid4().hex[:8]
        owner = Owner.objects.create(name=f"Extras owner {suffix}")
        user = User.objects.create(username=f"extras-{suffix}")
        group = Group.objects.create(name=f"Extras group {suffix}")
        tag = Tag.objects.create(name=f"Extras tag {suffix}", slug=f"extras-tag-{suffix}")
        site = Site.objects.create(name=f"Extras site {suffix}", slug=f"extras-site-{suffix}")
        site_type = ContentType.objects.get_for_model(Site)

        choice_set = CustomFieldChoiceSet.objects.create(
            name=f"Environment {suffix}",
            description="Deployment environments",
            extra_choices=(("prod", "Production"), ("dev", "Development")),
            choice_colors={"prod": "red", "dev": "blue"},
            order_alphabetically=True,
            owner=owner,
        )
        custom_field = CustomField.objects.create(
            type="select",
            name=f"environment_{suffix}",
            label="Environment",
            description="Deployment environment",
            choice_set=choice_set,
            owner=owner,
        )
        custom_field.object_types.set([site_type])

        custom_link = CustomLink.objects.create(
            name=f"Inventory {suffix}",
            link_text="Inventory",
            link_url="https://inventory.example.com/{{ object.pk }}",
            owner=owner,
        )
        custom_link.object_types.set([site_type])
        export_template = ExportTemplate.objects.create(
            name=f"Sites {suffix}",
            description="Site export",
            template_code="{{ queryset | length }}",
            mime_type="text/plain",
            file_extension="txt",
            owner=owner,
        )
        export_template.object_types.set([site_type])
        saved_filter = SavedFilter.objects.create(
            name=f"Active sites {suffix}",
            slug=f"active-sites-{suffix}",
            parameters={"status": ["active"]},
            user=user,
            shared=True,
            owner=owner,
        )
        saved_filter.object_types.set([site_type])
        table_config = TableConfig.objects.create(
            object_type=site_type,
            table="SiteTable",
            name=f"Operations {suffix}",
            columns=["name", "status"],
            ordering=["name"],
            user=user,
            shared=True,
        )
        profile = ConfigContextProfile.objects.create(
            name=f"Site schema {suffix}",
            description="Site context schema",
            schema={"type": "object"},
            owner=owner,
            comments="Portable profile",
        )
        profile.tags.set([tag])
        context = ConfigContext.objects.create(
            name=f"Site context {suffix}",
            profile=profile,
            description="Site defaults",
            data={"ntp": ["192.0.2.1"]},
            owner=owner,
        )
        context.sites.set([site])
        context.tags.set([tag])
        template = ConfigTemplate.objects.create(
            name=f"Network OS {suffix}",
            description="Device configuration",
            template_code="hostname {{ device.name }}",
            mime_type="text/plain",
            file_extension="conf",
            debug=False,
            owner=owner,
        )
        template.tags.set([tag])
        webhook = Webhook.objects.create(
            name=f"Automation {suffix}",
            description="Portable endpoint",
            payload_url="https://automation.example.com/hooks/netbox",
            http_method="POST",
            http_content_type="application/json",
            body_template="{{ data | json }}",
            secret="source-secret",
            additional_headers="Authorization: source-token",
            ca_file_path="/source/ca.pem",
            owner=owner,
        )
        webhook.tags.set([tag])
        notification_group = NotificationGroup.objects.create(
            name=f"Operators {suffix}",
            description="Operations notifications",
        )
        notification_group.groups.set([group])
        notification_group.users.set([user])
        event_rule = EventRule.objects.create(
            name=f"Site changes {suffix}",
            description="Notify automation",
            event_types=["object_created", "object_updated"],
            conditions=None,
            action_type="webhook",
            action_object=webhook,
            owner=owner,
        )
        event_rule.object_types.set([site_type])
        event_rule.tags.set([tag])

        objects = (
            choice_set,
            custom_field,
            custom_link,
            export_template,
            saved_filter,
            table_config,
            profile,
            context,
            template,
            webhook,
            notification_group,
            event_rule,
        )
        # Keep the model-to-kind association explicit; dictionary/set iteration
        # order is intentionally irrelevant to the provider graph.
        source_pks = {
            (kind, str(obj.pk))
            for kind, obj in zip(
                (
                    "custom_field_choice_set",
                    "custom_field",
                    "custom_link",
                    "export_template",
                    "saved_filter",
                    "table_config",
                    "config_context_profile",
                    "config_context",
                    "config_template",
                    "webhook",
                    "notification_group",
                    "event_rule",
                ),
                objects,
                strict=True,
            )
        }
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.resource_kind, record.target_object_id) in source_pks
        ]
        assert len(canonical) == 12
        custom_field_record = next(record for record in canonical if record.resource_kind == "custom_field")
        assert _extras_problems(
            [
                ApplicationRecord(
                    "custom_field",
                    custom_field_record.identity_key,
                    {**custom_field_record.attributes, "/type": "text"},
                    custom_field_record.relationships,
                )
            ],
            canonical,
        ) == (
            ["Changing the type of 1 existing Custom Fields is not supported; create a replacement field instead."]
        )
        webhook_record = next(record for record in canonical if record.resource_kind == "webhook")
        assert "source-secret" not in str(webhook_record.payload)
        assert "source-token" not in str(webhook_record.payload)
        assert "/ca_file_path" not in webhook_record.attributes

        expected = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        application_records = [
            ApplicationRecord(record.resource_kind, record.identity_key, record.attributes, record.relationships)
            for record in canonical
        ]

        event_rule.delete()
        context.delete()
        custom_field.delete()
        for obj in (
            notification_group,
            webhook,
            template,
            profile,
            table_config,
            saved_filter,
            export_template,
            custom_link,
            choice_set,
        ):
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

        recreated_webhook = object_cache[webhook_record.resource_kind, webhook_record.identity_key]
        recreated_webhook.secret = "destination-secret"
        recreated_webhook.additional_headers = "Authorization: destination-token"
        recreated_webhook.ca_file_path = "/destination/ca.pem"
        recreated_webhook.save()
        _write_object(
            recreated_webhook,
            next(record for record in application_records if record.resource_kind == "webhook"),
            target_by_key,
            object_cache,
            references,
        )
        recreated_webhook.refresh_from_db()
        assert recreated_webhook.secret == "destination-secret"
        assert recreated_webhook.additional_headers == "Authorization: destination-token"
        assert recreated_webhook.ca_file_path == "/destination/ca.pem"
