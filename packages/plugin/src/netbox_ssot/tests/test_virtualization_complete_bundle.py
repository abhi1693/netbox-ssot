from __future__ import annotations

from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

from dcim.models import DeviceRole, MACAddress, Platform, Site
from django.apps import apps
from django.test import TestCase
from extras.models import ConfigContext, Tag
from ipam.models import FHRPGroup, FHRPGroupAssignment, IPAddress, Service
from tenancy.models import Contact, ContactAssignment, ContactRole, Tenant
from users.models import Owner
from virtualization.models import (
    Cluster,
    ClusterGroup,
    ClusterType,
    VirtualDisk,
    VirtualMachine,
    VirtualMachineType,
    VMInterface,
)

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import _resolve_external_references, _write_object
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class VirtualizationCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_every_public_virtualization_resource_round_trips_through_snapshot_and_writer(self) -> None:
        suffix = uuid4().hex[:8]
        owner = Owner.objects.create(name=f"Virtualization owner {suffix}")
        tag = Tag.objects.create(name=f"Virtualization {suffix}", slug=f"virtualization-{suffix}")
        site = Site.objects.create(name=f"Virtualization {suffix}", slug=f"virtualization-{suffix}")
        tenant = Tenant.objects.create(name=f"Virtualization {suffix}", slug=f"virtualization-{suffix}")
        platform = Platform.objects.create(name=f"Linux {suffix}", slug=f"linux-{suffix}")
        role = DeviceRole.objects.create(name=f"Server {suffix}", slug=f"server-{suffix}", color="2196f3")

        cluster_type = ClusterType.objects.create(name=f"Hypervisor {suffix}", slug=f"hypervisor-{suffix}", owner=owner)
        cluster_group = ClusterGroup.objects.create(
            name=f"Production {suffix}", slug=f"production-{suffix}", owner=owner
        )
        cluster = Cluster.objects.create(
            name=f"Cluster {suffix}",
            type=cluster_type,
            group=cluster_group,
            tenant=tenant,
            scope=site,
            status="active",
            owner=owner,
        )
        virtual_machine_type = VirtualMachineType.objects.create(
            name=f"General purpose {suffix}",
            slug=f"general-purpose-{suffix}",
            default_platform=platform,
            default_vcpus=Decimal("2.00"),
            default_memory=4096,
            owner=owner,
        )
        virtual_machine = VirtualMachine.objects.create(
            name=f"vm-{suffix}",
            virtual_machine_type=virtual_machine_type,
            site=site,
            cluster=cluster,
            tenant=tenant,
            platform=platform,
            role=role,
            status="active",
            start_on_boot="on",
            vcpus=Decimal("4.00"),
            memory=8192,
            disk=100,
            serial=f"serial-{suffix}",
            local_context_data={"purpose": "round-trip"},
            owner=owner,
        )
        parent_interface = VMInterface.objects.create(
            virtual_machine=virtual_machine,
            name="bond0",
            enabled=True,
            mtu=9000,
            description="Parent interface",
            owner=owner,
        )
        child_interface = VMInterface.objects.create(
            virtual_machine=virtual_machine,
            name="eth0",
            enabled=True,
            parent=parent_interface,
            mtu=1500,
            description="Workload interface",
            owner=owner,
        )
        virtual_disk = VirtualDisk.objects.create(
            virtual_machine=virtual_machine,
            name="root",
            description="Root volume",
            size=100,
            owner=owner,
        )

        objects = (
            cluster_type,
            cluster_group,
            cluster,
            virtual_machine_type,
            virtual_machine,
            parent_interface,
            child_interface,
            virtual_disk,
        )
        for obj in objects:
            obj.tags.set([tag])

        source_pks = {(obj._meta.label_lower, str(obj.pk)) for obj in objects}
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.target_object_type, record.target_object_id) in source_pks
        ]
        assert len(canonical) == len(objects)
        assert next(record for record in canonical if record.resource_kind == "cluster").relationships["scope_site"]
        assert next(
            record
            for record in canonical
            if record.resource_kind == "vm_interface" and record.attributes["/name"] == "eth0"
        ).relationships["parent"]

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

    def test_virtualization_targets_are_preserved_across_generic_app_relationships(self) -> None:
        suffix = uuid4().hex[:8]
        site = Site.objects.create(name=f"Virtual target {suffix}", slug=f"virtual-target-{suffix}")
        cluster_type = ClusterType.objects.create(name=f"Target {suffix}", slug=f"target-{suffix}")
        cluster_group = ClusterGroup.objects.create(name=f"Target {suffix}", slug=f"target-{suffix}")
        cluster = Cluster.objects.create(name=f"Target {suffix}", type=cluster_type, group=cluster_group, scope=site)
        virtual_machine = VirtualMachine.objects.create(name=f"target-{suffix}", cluster=cluster, site=site)
        interface = VMInterface.objects.create(virtual_machine=virtual_machine, name="eth0")

        address = IPAddress.objects.create(address="192.0.2.1/32", assigned_object=interface)
        fhrp_group = FHRPGroup.objects.create(protocol="vrrp3", group_id=100)
        fhrp_assignment = FHRPGroupAssignment.objects.create(group=fhrp_group, interface=interface, priority=100)
        service = Service.objects.create(parent=virtual_machine, name=f"API {suffix}", protocol="tcp", ports=[443])
        mac_address = MACAddress.objects.create(mac_address="00:11:22:33:44:66", assigned_object=interface)
        config_context = ConfigContext.objects.create(name=f"Virtual context {suffix}", data={"role": "compute"})
        config_context.cluster_types.add(cluster_type)
        config_context.cluster_groups.add(cluster_group)
        config_context.clusters.add(cluster)
        contact = Contact.objects.create(name=f"Operator {suffix}")
        contact_role = ContactRole.objects.create(name=f"Technical {suffix}", slug=f"technical-{suffix}")
        contact_assignment = ContactAssignment.objects.create(
            object=virtual_machine,
            contact=contact,
            role=contact_role,
        )

        objects = (
            address,
            fhrp_assignment,
            service,
            mac_address,
            config_context,
            contact_assignment,
        )
        source_pks = {(obj._meta.label_lower, str(obj.pk)) for obj in objects}
        records = {
            record.resource_kind: record
            for record in load_netbox_target_records()
            if (record.target_object_type, record.target_object_id) in source_pks
        }

        assert records["ip_address"].relationships["assigned_vm_interface"]
        assert records["fhrp_group_assignment"].relationships["interface_vm_interface"]
        assert records["service"].relationships["parent_virtual_machine"]
        assert records["mac_address"].relationships["assigned_vm_interface"]
        assert records["config_context"].relationships.keys() >= {
            "cluster_type",
            "cluster_group",
            "cluster",
        }
        assert records["contact_assignment"].relationships["object_virtual_machine"]
