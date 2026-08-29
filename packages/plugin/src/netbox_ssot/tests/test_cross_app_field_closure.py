from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from dcim.models import (
    Device,
    DeviceBay,
    DeviceBayTemplate,
    DeviceRole,
    DeviceType,
    Interface,
    InterfaceTemplate,
    MACAddress,
    Manufacturer,
    Site,
)
from django.apps import apps
from django.test import TestCase
from ipam.models import VLAN, VRF, IPAddress, VLANTranslationPolicy
from virtualization.models import Cluster, ClusterType, VirtualMachine, VMInterface
from wireless.models import WirelessLAN

from netbox_ssot.application.planning import ApplicationRecord
from netbox_ssot.application.service import _write_deferred_relationships, _write_object
from netbox_ssot.planning.netbox_target import load_netbox_target_records


class CrossAppFieldClosureTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_cross_app_selectors_and_memberships_round_trip_without_partial_clear_risk(self) -> None:
        suffix = uuid4().hex[:8]
        site = Site.objects.create(name=f"Closure {suffix}", slug=f"closure-{suffix}")
        manufacturer = Manufacturer.objects.create(name=f"Closure {suffix}", slug=f"closure-{suffix}")
        role = DeviceRole.objects.create(name=f"Closure {suffix}", slug=f"closure-{suffix}", color="2196f3")
        parent_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model=f"Parent {suffix}",
            slug=f"parent-{suffix}",
            subdevice_role="parent",
        )
        child_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model=f"Child {suffix}",
            slug=f"child-{suffix}",
            subdevice_role="child",
        )
        InterfaceTemplate.objects.create(device_type=parent_type, name="eth0", type="1000base-t")
        DeviceBayTemplate.objects.create(device_type=parent_type, name="slot0", enabled=True)

        cluster_type = ClusterType.objects.create(name=f"Closure {suffix}", slug=f"closure-{suffix}")
        cluster = Cluster.objects.create(name=f"Closure {suffix}", type=cluster_type, scope=site)
        device = Device.objects.create(
            name=f"parent-{suffix}",
            device_type=parent_type,
            role=role,
            site=site,
            cluster=cluster,
        )
        child = Device.objects.create(
            name=f"child-{suffix}",
            device_type=child_type,
            role=role,
            site=site,
        )
        device_bay = DeviceBay.objects.get(device=device, name="slot0")
        device_bay.installed_device = child
        device_bay.full_clean()
        device_bay.save()

        service_vlan = VLAN.objects.create(
            site=site,
            vid=100,
            name=f"Service {suffix}",
            status="active",
            qinq_role="service",
        )
        customer_vlan = VLAN.objects.create(
            site=site,
            vid=101,
            name=f"Customer {suffix}",
            status="active",
            qinq_role="customer",
            qinq_svlan=service_vlan,
        )
        tagged_vlan = VLAN.objects.create(
            site=site,
            vid=102,
            name=f"Tagged {suffix}",
            status="active",
            qinq_role="customer",
            qinq_svlan=service_vlan,
        )
        vrf = VRF.objects.create(name=f"Closure {suffix}", rd=f"65000:{int(suffix[:4], 16)}")
        policy = VLANTranslationPolicy.objects.create(name=f"Closure {suffix}")
        wireless_lan = WirelessLAN.objects.create(
            ssid=f"closure-{suffix}",
            status="active",
            vlan=customer_vlan,
        )
        interface = Interface.objects.get(device=device, name="eth0")
        interface.mode = "tagged"
        interface.untagged_vlan = customer_vlan
        interface.vlan_translation_policy = policy
        interface.vrf = vrf
        interface.full_clean()
        interface.save()
        interface.tagged_vlans.set([tagged_vlan])
        interface.wireless_lans.set([wireless_lan])
        assert set(interface.tagged_vlans.all()) == {tagged_vlan}
        qinq_interface = Interface.objects.create(
            device=device,
            name="eth1",
            type="1000base-t",
            mode="q-in-q",
            qinq_svlan=service_vlan,
        )

        primary_ip = IPAddress.objects.create(address="192.0.2.10/32", assigned_object=interface)
        oob_ip = IPAddress.objects.create(address="192.0.2.11/32", assigned_object=interface)
        primary_ip.refresh_from_db()
        oob_ip.refresh_from_db()
        primary_mac = MACAddress.objects.create(mac_address="00:11:22:33:44:70", assigned_object=interface)
        interface.primary_mac_address = primary_mac
        interface.save()
        device.primary_ip4 = primary_ip
        device.oob_ip = oob_ip
        device.full_clean()
        device.save()

        virtual_machine = VirtualMachine.objects.create(name=f"vm-{suffix}", cluster=cluster, site=site)
        vm_interface = VMInterface.objects.create(virtual_machine=virtual_machine, name="eth0")
        vm_ip = IPAddress.objects.create(address="198.51.100.10/32", assigned_object=vm_interface)
        vm_ip.refresh_from_db()
        vm_mac = MACAddress.objects.create(mac_address="00:11:22:33:44:71", assigned_object=vm_interface)
        vm_interface.primary_mac_address = vm_mac
        vm_interface.full_clean()
        vm_interface.save()
        virtual_machine.primary_ip4 = vm_ip
        virtual_machine.full_clean()
        virtual_machine.save()

        selected = {
            ("device", str(device.pk)),
            ("device_bay", str(device_bay.pk)),
            ("interface", str(interface.pk)),
            ("interface", str(qinq_interface.pk)),
            ("virtual_machine", str(virtual_machine.pk)),
            ("vm_interface", str(vm_interface.pk)),
        }
        full_target = load_netbox_target_records()
        canonical = [
            record
            for record in full_target
            if (record.resource_kind, record.target_object_id) in selected
        ]
        by_kind = {record.resource_kind: record for record in canonical if record.resource_kind != "interface"}
        interface_record = next(record for record in canonical if record.target_object_id == str(interface.pk))
        qinq_interface_record = next(
            record for record in canonical if record.target_object_id == str(qinq_interface.pk)
        )
        assert by_kind["device"].relationships.keys() >= {"cluster", "primary_ip4", "oob_ip"}
        assert by_kind["device_bay"].relationships["installed_device"]
        expected_interface_relationships = {
            "untagged_vlan",
            "tagged_vlan",
            "vlan_translation_policy",
            "vrf",
            "wireless_lan",
            "primary_mac_address",
        }
        assert interface_record.relationships.keys() >= expected_interface_relationships, interface_record.relationships
        assert qinq_interface_record.relationships["qinq_svlan"]
        assert by_kind["virtual_machine"].relationships["primary_ip4"]
        assert by_kind["vm_interface"].relationships["primary_mac_address"]

        partial_target = load_netbox_target_records(datasets=("devices",))
        partial = {
            record.resource_kind: record
            for record in partial_target
            if (record.resource_kind, record.target_object_id) in selected and record.resource_kind != "interface"
        }
        partial_interface = next(
            record
            for record in partial_target
            if record.resource_kind == "interface" and record.target_object_id == str(interface.pk)
        )
        assert "/manage_primary_ip_selectors" not in partial["device"].attributes
        assert "primary_ip4" not in partial["device"].relationships
        assert "/manage_wireless_lans" not in partial_interface.attributes
        assert "wireless_lan" not in partial_interface.relationships
        assert "/manage_primary_mac_selector" not in partial["vm_interface"].attributes
        assert "primary_mac_address" not in partial["vm_interface"].relationships

        device.cluster = None
        device.primary_ip4 = None
        device.oob_ip = None
        device.save()
        device_bay.installed_device = None
        device_bay.save()
        interface.untagged_vlan = None
        interface.vlan_translation_policy = None
        interface.vrf = None
        interface.primary_mac_address = None
        interface.save()
        interface.tagged_vlans.clear()
        interface.wireless_lans.clear()
        qinq_interface.qinq_svlan = None
        qinq_interface.save()
        virtual_machine.primary_ip4 = None
        virtual_machine.save()
        vm_interface.primary_mac_address = None
        vm_interface.save()

        target_by_key = {(record.resource_kind, record.identity_key): record for record in full_target}
        records = [
            ApplicationRecord(record.resource_kind, record.identity_key, record.attributes, record.relationships)
            for record in canonical
        ]
        objects_by_target = {
            ("device", str(device.pk)): device,
            ("device_bay", str(device_bay.pk)): device_bay,
            ("interface", str(interface.pk)): interface,
            ("interface", str(qinq_interface.pk)): qinq_interface,
            ("virtual_machine", str(virtual_machine.pk)): virtual_machine,
            ("vm_interface", str(vm_interface.pk)): vm_interface,
        }
        object_cache = {
            application_record.key: objects_by_target[
                (canonical_record.resource_kind, canonical_record.target_object_id)
            ]
            for application_record, canonical_record in zip(records, canonical, strict=True)
        }
        for record in records:
            _write_object(object_cache[record.key], record, target_by_key, object_cache, {})
        _write_deferred_relationships(records, target_by_key, object_cache)

        device.refresh_from_db()
        device_bay.refresh_from_db()
        interface.refresh_from_db()
        qinq_interface.refresh_from_db()
        virtual_machine.refresh_from_db()
        vm_interface.refresh_from_db()
        assert device.cluster == cluster
        assert device.primary_ip4 == primary_ip
        assert device.oob_ip == oob_ip
        assert device_bay.installed_device == child
        assert interface.untagged_vlan == customer_vlan
        assert qinq_interface.qinq_svlan == service_vlan
        assert interface.vlan_translation_policy == policy
        assert interface.vrf == vrf
        assert set(interface.tagged_vlans.all()) == {tagged_vlan}
        assert set(interface.wireless_lans.all()) == {wireless_lan}
        assert interface.primary_mac_address == primary_mac
        assert virtual_machine.primary_ip4 == vm_ip
        assert vm_interface.primary_mac_address == vm_mac
