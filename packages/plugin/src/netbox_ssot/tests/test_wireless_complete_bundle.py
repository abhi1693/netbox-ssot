from __future__ import annotations

from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

from dcim.choices import InterfaceTypeChoices
from dcim.models import Device, DeviceRole, DeviceType, Interface, Location, Manufacturer, Region, Site, SiteGroup
from django.apps import apps
from django.test import TestCase
from extras.models import Tag
from ipam.models import VLAN
from netbox.choices import DistanceUnitChoices
from tenancy.models import Tenant
from users.models import Owner
from wireless.choices import WirelessAuthCipherChoices, WirelessAuthTypeChoices
from wireless.models import WirelessLAN, WirelessLANGroup, WirelessLink

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import _resolve_external_references, _write_object
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class WirelessCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_every_public_wireless_resource_round_trips_through_snapshot_and_writer(self) -> None:
        suffix = uuid4().hex[:8]
        owner = Owner.objects.create(name=f"Wireless owner {suffix}")
        tag = Tag.objects.create(name=f"Wireless {suffix}", slug=f"wireless-{suffix}")
        tenant = Tenant.objects.create(name=f"Wireless {suffix}", slug=f"wireless-{suffix}")
        region = Region.objects.create(name=f"Wireless {suffix}", slug=f"wireless-{suffix}")
        site_group = SiteGroup.objects.create(name=f"Wireless {suffix}", slug=f"wireless-{suffix}")
        site = Site.objects.create(
            name=f"Wireless {suffix}",
            slug=f"wireless-{suffix}",
            region=region,
            group=site_group,
        )
        location = Location.objects.create(site=site, name=f"Wireless {suffix}", slug=f"wireless-{suffix}")
        vlan = VLAN.objects.create(site=site, vid=100, name=f"Wireless {suffix}", status="active")

        manufacturer = Manufacturer.objects.create(name=f"Wireless {suffix}", slug=f"wireless-{suffix}")
        role = DeviceRole.objects.create(name=f"Wireless {suffix}", slug=f"wireless-{suffix}", color="2196f3")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model=f"Wireless {suffix}")
        device_a = Device.objects.create(
            name=f"wireless-a-{suffix}", device_type=device_type, role=role, site=site, status="active"
        )
        device_b = Device.objects.create(
            name=f"wireless-b-{suffix}", device_type=device_type, role=role, site=site, status="active"
        )
        interface_a = Interface.objects.create(
            device=device_a,
            name="radio0",
            type=InterfaceTypeChoices.TYPE_80211AX,
        )
        interface_b = Interface.objects.create(
            device=device_b,
            name="radio0",
            type=InterfaceTypeChoices.TYPE_80211AX,
        )

        root_group = WirelessLANGroup.objects.create(
            name=f"Wireless root {suffix}", slug=f"wireless-root-{suffix}", owner=owner
        )
        child_group = WirelessLANGroup.objects.create(
            name=f"Wireless child {suffix}",
            slug=f"wireless-child-{suffix}",
            parent=root_group,
            owner=owner,
        )
        wireless_lans = tuple(
            WirelessLAN.objects.create(
                ssid=f"portable-{suffix}",
                group=child_group,
                status="active",
                vlan=vlan,
                scope=scope,
                tenant=tenant,
                auth_type=WirelessAuthTypeChoices.TYPE_WPA_PERSONAL,
                auth_cipher=WirelessAuthCipherChoices.CIPHER_AES,
                auth_psk="source-lan-secret",
                owner=owner,
            )
            for scope in (region, site_group, site, location)
        )
        wireless_link = WirelessLink.objects.create(
            interface_a=interface_a,
            interface_b=interface_b,
            ssid=f"backhaul-{suffix}",
            status="connected",
            tenant=tenant,
            auth_type=WirelessAuthTypeChoices.TYPE_WPA_PERSONAL,
            auth_cipher=WirelessAuthCipherChoices.CIPHER_AES,
            auth_psk="source-link-secret",
            distance=Decimal("0.500"),
            distance_unit=DistanceUnitChoices.UNIT_KILOMETER,
            owner=owner,
        )

        objects = (root_group, child_group, *wireless_lans, wireless_link)
        for obj in objects:
            obj.tags.set([tag])

        source_pks = {(obj._meta.label_lower, str(obj.pk)) for obj in objects}
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.target_object_type, record.target_object_id) in source_pks
        ]
        assert len(canonical) == len(objects)
        assert {name for record in canonical for name in record.relationships if name.startswith("scope_")} == {
            "scope_region",
            "scope_site_group",
            "scope_site",
            "scope_location",
        }
        assert "source-lan-secret" not in str([record.payload for record in canonical])
        assert "source-link-secret" not in str([record.payload for record in canonical])
        assert all("/auth_psk" not in record.attributes for record in canonical)

        expected = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        application_records = [
            ApplicationRecord(record.resource_kind, record.identity_key, record.attributes, record.relationships)
            for record in canonical
        ]

        wireless_link.delete()
        for wireless_lan in wireless_lans:
            wireless_lan.delete()
        child_group.delete()
        root_group.delete()

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

        lan_record = next(record for record in application_records if record.resource_kind == "wireless_lan")
        recreated_lan = object_cache[lan_record.key]
        assert recreated_lan.auth_psk == ""
        recreated_lan.auth_psk = "destination-lan-secret"
        recreated_lan.save()
        _write_object(recreated_lan, lan_record, target_by_key, object_cache, references)
        recreated_lan.refresh_from_db()
        assert recreated_lan.auth_psk == "destination-lan-secret"

        link_record = next(record for record in application_records if record.resource_kind == "wireless_link")
        recreated_link = object_cache[link_record.key]
        assert recreated_link.auth_psk == ""
        recreated_link.auth_psk = "destination-link-secret"
        recreated_link.save()
        _write_object(recreated_link, link_record, target_by_key, object_cache, references)
        recreated_link.refresh_from_db()
        assert recreated_link.auth_psk == "destination-link-secret"
