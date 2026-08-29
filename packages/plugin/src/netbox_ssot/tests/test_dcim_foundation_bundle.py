from __future__ import annotations

from typing import ClassVar

from dcim.models import (
    DeviceRole,
    DeviceType,
    Location,
    Manufacturer,
    Platform,
    Rack,
    RackGroup,
    RackRole,
    RackType,
    Site,
)
from django.apps import apps
from django.test import TestCase
from extras.models import ConfigTemplate

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import _resolve_external_references, _write_object
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records

DCIM_FOUNDATION_KINDS = frozenset(
    {
        "manufacturer",
        "device_role",
        "platform",
        "device_type",
        "rack_group",
        "rack_role",
        "rack_type",
        "rack",
    }
)


class DCIMFoundationBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_target_snapshot_records_can_be_recreated_through_the_apply_writer(self) -> None:
        template = ConfigTemplate.objects.create(
            name="DCIM foundation template",
            template_code="{{ device.name }}",
        )
        site = Site.objects.create(name="Foundation site", slug="foundation-site")
        location = Location.objects.create(
            site=site,
            name="Foundation room",
            slug="foundation-room",
        )
        manufacturer = Manufacturer.objects.create(
            name="Foundation manufacturer",
            slug="foundation-manufacturer",
            description="Hardware maker",
            comments="Manufacturer notes",
        )
        parent_role = DeviceRole.objects.create(
            name="Foundation network",
            slug="foundation-network",
            color="2196f3",
            vm_role=False,
            config_template=template,
            description="Network devices",
        )
        DeviceRole.objects.create(
            parent=parent_role,
            name="Foundation leaf",
            slug="foundation-leaf",
            color="4caf50",
            vm_role=False,
            config_template=template,
            description="Leaf devices",
        )
        platform = Platform.objects.create(
            name="Foundation OS",
            slug="foundation-os",
            manufacturer=manufacturer,
            config_template=template,
            description="Network operating system",
        )
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            default_platform=platform,
            model="Foundation switch 48",
            slug="foundation-switch-48",
            part_number="FSW48",
            u_height=1,
            exclude_from_utilization=False,
            is_full_depth=True,
            subdevice_role="parent",
            airflow="front-to-rear",
            weight=7.5,
            weight_unit="kg",
            description="48-port switch",
        )
        rack_group = RackGroup.objects.create(
            name="Foundation row",
            slug="foundation-row",
            description="First row",
        )
        rack_role = RackRole.objects.create(
            name="Foundation compute",
            slug="foundation-compute",
            color="9c27b0",
            description="Compute racks",
        )
        rack_type = RackType.objects.create(
            manufacturer=manufacturer,
            model="Foundation R42",
            slug="foundation-r42",
            form_factor="4-post-cabinet",
            width=19,
            u_height=42,
            starting_unit=1,
            desc_units=False,
            outer_width=600,
            outer_height=2000,
            outer_depth=1200,
            outer_unit="mm",
            mounting_depth=1000,
            weight=100.25,
            max_weight=1500,
            weight_unit="kg",
            description="Standard rack",
        )
        rack = Rack.objects.create(
            name="Foundation A01",
            facility_id="FOUNDATION-A01",
            site=site,
            location=location,
            group=rack_group,
            status="active",
            role=rack_role,
            serial="RACK-SERIAL",
            asset_tag="RACK-ASSET",
            rack_type=rack_type,
            airflow="front-to-rear",
            description="Primary rack",
        )

        source_pks = {
            ("manufacturer", str(manufacturer.pk)),
            ("device_role", str(parent_role.pk)),
            ("device_role", str(parent_role.children.get().pk)),
            ("platform", str(platform.pk)),
            ("device_type", str(device_type.pk)),
            ("rack_group", str(rack_group.pk)),
            ("rack_role", str(rack_role.pk)),
            ("rack_type", str(rack_type.pk)),
            ("rack", str(rack.pk)),
        }
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.resource_kind, record.target_object_id) in source_pks
        ]
        assert len(canonical) == 9
        expected_payloads = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        application_records = [
            ApplicationRecord(
                resource_kind=record.resource_kind,
                identity_key=record.identity_key,
                attributes=record.attributes,
                relationships=record.relationships,
            )
            for record in canonical
        ]

        rack.delete()
        device_type.delete()
        rack_type.delete()
        platform.delete()
        parent_role.children.get().delete()
        parent_role.delete()
        rack_role.delete()
        rack_group.delete()
        manufacturer.delete()

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
            if record.resource_kind in DCIM_FOUNDATION_KINDS
            and (record.resource_kind, record.identity_key) in expected_payloads
        }
        assert recreated == expected_payloads
