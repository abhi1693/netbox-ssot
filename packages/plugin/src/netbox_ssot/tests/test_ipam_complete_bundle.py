from __future__ import annotations

from datetime import date
from typing import ClassVar
from uuid import uuid4

import netaddr
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from extras.models import Tag
from ipam.models import (
    ASN,
    RIR,
    VLAN,
    VRF,
    Aggregate,
    ASNRange,
    FHRPGroup,
    FHRPGroupAssignment,
    IPAddress,
    IPRange,
    Prefix,
    Role,
    RouteTarget,
    Service,
    ServiceTemplate,
    VLANGroup,
    VLANTranslationPolicy,
    VLANTranslationRule,
)
from tenancy.models import Tenant
from users.models import Owner

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import _resolve_external_references, _write_object
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class IPAMCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_every_public_ipam_resource_round_trips_through_snapshot_and_writer(self) -> None:
        suffix = uuid4().hex[:8]
        owner = Owner.objects.create(name=f"IPAM owner {suffix}")
        tag = Tag.objects.create(name=f"IPAM {suffix}", slug=f"ipam-{suffix}")
        tenant = Tenant.objects.create(name=f"IPAM {suffix}", slug=f"ipam-{suffix}")
        site = Site.objects.create(name=f"IPAM {suffix}", slug=f"ipam-{suffix}")
        manufacturer = Manufacturer.objects.create(name=f"IPAM {suffix}", slug=f"ipam-{suffix}")
        device_role = DeviceRole.objects.create(name=f"IPAM {suffix}", slug=f"ipam-{suffix}", color="2196f3")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model=f"IPAM {suffix}")
        device = Device.objects.create(
            name=f"ipam-{suffix}", device_type=device_type, role=device_role, site=site, status="active"
        )
        interface = Interface.objects.create(device=device, name="eth0", type="1000base-t")

        rir = RIR.objects.create(name=f"Registry {suffix}", slug=f"registry-{suffix}", is_private=True, owner=owner)
        role = Role.objects.create(
            name=f"Infrastructure {suffix}", slug=f"infrastructure-{suffix}", weight=100, owner=owner
        )
        asn = ASN.objects.create(asn=4_200_000_000 + int(suffix[:5], 16), rir=rir, role=role, tenant=tenant)
        asn_range = ASNRange.objects.create(
            name=f"Private range {suffix}",
            slug=f"private-range-{suffix}",
            rir=rir,
            start=4_100_000_000 + int(suffix[:4], 16) * 2,
            end=4_100_000_001 + int(suffix[:4], 16) * 2,
            tenant=tenant,
            owner=owner,
        )
        route_target_import = RouteTarget.objects.create(name=f"65000:{int(suffix[:4], 16)}", tenant=tenant)
        route_target_export = RouteTarget.objects.create(name=f"65001:{int(suffix[:4], 16)}", tenant=tenant)
        vrf = VRF.objects.create(
            name=f"Production {suffix}", rd=f"64512:{int(suffix[:4], 16)}", tenant=tenant, owner=owner
        )
        vrf.import_targets.add(route_target_import)
        vrf.export_targets.add(route_target_export)
        aggregate = Aggregate.objects.create(
            prefix="198.18.0.0/15", rir=rir, tenant=tenant, date_added=date(2026, 8, 29), owner=owner
        )
        vlan_group = VLANGroup.objects.create(
            name=f"Site VLANs {suffix}", slug=f"site-vlans-{suffix}", scope=site, tenant=tenant, owner=owner
        )
        vlan = VLAN.objects.create(
            site=site,
            group=vlan_group,
            vid=100,
            name=f"Applications {suffix}",
            tenant=tenant,
            status="active",
            role=role,
            owner=owner,
        )
        translation_policy = VLANTranslationPolicy.objects.create(name=f"Translation {suffix}", owner=owner)
        translation_rule = VLANTranslationRule.objects.create(
            policy=translation_policy, local_vid=100, remote_vid=200, description="Provider handoff"
        )
        prefix = Prefix.objects.create(
            prefix="10.100.0.0/24",
            vrf=vrf,
            scope=site,
            tenant=tenant,
            vlan=vlan,
            status="active",
            role=role,
            owner=owner,
        )
        ip_range = IPRange.objects.create(
            start_address=netaddr.IPNetwork("10.100.0.10/24"),
            end_address=netaddr.IPNetwork("10.100.0.20/24"),
            vrf=vrf,
            tenant=tenant,
            status="active",
            role=role,
            owner=owner,
        )
        fhrp_group = FHRPGroup.objects.create(
            protocol="vrrp3",
            group_id=100,
            name=f"Gateway {suffix}",
            auth_type="plaintext",
            auth_key="source-secret",
            owner=owner,
        )
        inside = IPAddress.objects.create(
            address="10.100.0.1/24", vrf=vrf, tenant=tenant, status="active", role="vrrp", owner=owner
        )
        outside = IPAddress.objects.create(
            address="192.0.2.1/32",
            vrf=vrf,
            tenant=tenant,
            status="active",
            assigned_object=fhrp_group,
            nat_inside=inside,
            owner=owner,
        )
        assignment = FHRPGroupAssignment.objects.create(group=fhrp_group, interface=interface, priority=110)
        service_template = ServiceTemplate.objects.create(
            name=f"HTTPS {suffix}", protocol="tcp", ports=[443], owner=owner
        )
        service = Service.objects.create(
            parent=fhrp_group,
            name=f"API {suffix}",
            protocol="tcp",
            ports=[443, 8443],
            owner=owner,
        )
        service.ipaddresses.set([inside, outside])

        tagged = (
            rir,
            role,
            asn,
            asn_range,
            route_target_import,
            route_target_export,
            vrf,
            aggregate,
            vlan_group,
            vlan,
            prefix,
            ip_range,
            fhrp_group,
            inside,
            outside,
            service_template,
            service,
        )
        for obj in tagged:
            obj.tags.set([tag])

        objects = (
            rir,
            role,
            asn,
            asn_range,
            route_target_import,
            route_target_export,
            vrf,
            aggregate,
            vlan_group,
            vlan,
            translation_policy,
            translation_rule,
            prefix,
            ip_range,
            fhrp_group,
            inside,
            outside,
            assignment,
            service_template,
            service,
        )
        source_pks = {(obj._meta.label_lower, str(obj.pk)) for obj in objects}
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.target_object_type, record.target_object_id) in source_pks
        ]
        assert len(canonical) == len(objects)
        fhrp_record = next(record for record in canonical if record.resource_kind == "fhrp_group")
        assert "source-secret" not in str(fhrp_record.payload)
        assert "/auth_key" not in fhrp_record.attributes
        assert next(record for record in canonical if record.resource_kind == "asn").relationships["role"]

        expected = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        application_records = []
        for record in canonical:
            attributes = record.attributes
            if record.resource_kind == "vlan_group":
                assert attributes["/vid_ranges"] == [{"start": 1, "end": 4094}]
                attributes = {**attributes, "/vid_ranges": [[1, 4094]]}
            application_records.append(
                ApplicationRecord(record.resource_kind, record.identity_key, attributes, record.relationships)
            )

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

        recreated_group = object_cache[fhrp_record.resource_kind, fhrp_record.identity_key]
        assert recreated_group.auth_key == ""
        recreated_group.auth_key = "destination-secret"
        recreated_group.save()
        _write_object(
            recreated_group,
            next(record for record in application_records if record.resource_kind == "fhrp_group"),
            target_by_key,
            object_cache,
            references,
        )
        recreated_group.refresh_from_db()
        assert recreated_group.auth_key == "destination-secret"

        assert (
            ContentType.objects.get_for_model(
                object_cache[
                    (
                        "vlan_group",
                        next(
                            record.identity_key
                            for record in application_records
                            if record.resource_kind == "vlan_group"
                        ),
                    )
                ].scope
            ).model
            == "site"
        )
