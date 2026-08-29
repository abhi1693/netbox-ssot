from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from django.apps import apps
from django.test import TestCase
from extras.models import Tag
from ipam.models import VLAN, IPAddress, RouteTarget
from tenancy.models import Contact, ContactAssignment, ContactRole, Tenant
from users.models import Owner
from virtualization.models import Cluster, ClusterType, VirtualMachine, VMInterface
from vpn.models import (
    L2VPN,
    IKEPolicy,
    IKEProposal,
    IPSecPolicy,
    IPSecProfile,
    IPSecProposal,
    L2VPNTermination,
    Tunnel,
    TunnelGroup,
    TunnelTermination,
)

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import _resolve_external_references, _write_object
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class VPNCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_every_public_vpn_resource_round_trips_through_snapshot_and_writer(self) -> None:
        suffix = uuid4().hex[:8]
        owner = Owner.objects.create(name=f"VPN owner {suffix}")
        tag = Tag.objects.create(name=f"VPN {suffix}", slug=f"vpn-{suffix}")
        tenant = Tenant.objects.create(name=f"VPN {suffix}", slug=f"vpn-{suffix}")
        site = Site.objects.create(name=f"VPN {suffix}", slug=f"vpn-{suffix}")
        manufacturer = Manufacturer.objects.create(name=f"VPN {suffix}", slug=f"vpn-{suffix}")
        role = DeviceRole.objects.create(name=f"VPN {suffix}", slug=f"vpn-{suffix}", color="2196f3")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model=f"VPN {suffix}")
        device = Device.objects.create(
            name=f"vpn-{suffix}", device_type=device_type, role=role, site=site, status="active"
        )
        interface = Interface.objects.create(device=device, name="tunnel0", type="virtual")
        cluster_type = ClusterType.objects.create(name=f"VPN {suffix}", slug=f"vpn-{suffix}")
        cluster = Cluster.objects.create(name=f"VPN {suffix}", type=cluster_type, scope=site)
        virtual_machine = VirtualMachine.objects.create(name=f"vpn-{suffix}", site=site, cluster=cluster)
        vm_interface = VMInterface.objects.create(virtual_machine=virtual_machine, name="tunnel0")
        outside_ip = IPAddress.objects.create(address="192.0.2.1/32")
        vlan = VLAN.objects.create(site=site, vid=100, name=f"VPN {suffix}", status="active")
        import_target = RouteTarget.objects.create(name=f"64512:{int(suffix[:4], 16)}")
        export_target = RouteTarget.objects.create(name=f"64513:{int(suffix[:4], 16)}")

        ike_proposal = IKEProposal.objects.create(
            name=f"IKE proposal {suffix}",
            authentication_method="preshared-keys",
            encryption_algorithm="aes-256-cbc",
            authentication_algorithm="hmac-sha256",
            group=14,
            sa_lifetime=3600,
            owner=owner,
        )
        ike_policy = IKEPolicy.objects.create(
            name=f"IKE policy {suffix}",
            version=1,
            mode="main",
            preshared_key="source-secret",
            owner=owner,
        )
        ike_policy.proposals.set([ike_proposal])
        ipsec_proposal = IPSecProposal.objects.create(
            name=f"IPsec proposal {suffix}",
            encryption_algorithm="aes-256-cbc",
            authentication_algorithm="hmac-sha256",
            sa_lifetime_seconds=3600,
            sa_lifetime_data=1_000_000,
            owner=owner,
        )
        ipsec_policy = IPSecPolicy.objects.create(name=f"IPsec policy {suffix}", pfs_group=14, owner=owner)
        ipsec_policy.proposals.set([ipsec_proposal])
        ipsec_profile = IPSecProfile.objects.create(
            name=f"IPsec profile {suffix}",
            mode="esp",
            ike_policy=ike_policy,
            ipsec_policy=ipsec_policy,
            owner=owner,
        )
        tunnel_group = TunnelGroup.objects.create(name=f"WAN {suffix}", slug=f"wan-{suffix}", owner=owner)
        tunnel = Tunnel.objects.create(
            name=f"Tunnel {suffix}",
            status="active",
            group=tunnel_group,
            encapsulation="ipsec-tunnel",
            ipsec_profile=ipsec_profile,
            tenant=tenant,
            tunnel_id=int(suffix[:6], 16),
            owner=owner,
        )
        physical_termination = TunnelTermination.objects.create(
            tunnel=tunnel,
            role="peer",
            termination=interface,
            outside_ip=outside_ip,
        )
        virtual_termination = TunnelTermination.objects.create(
            tunnel=tunnel,
            role="peer",
            termination=vm_interface,
        )
        l2vpn = L2VPN.objects.create(
            name=f"L2VPN {suffix}",
            slug=f"l2vpn-{suffix}",
            type="vpls",
            status="active",
            identifier=int(suffix[:6], 16),
            tenant=tenant,
            owner=owner,
        )
        l2vpn.import_targets.set([import_target])
        l2vpn.export_targets.set([export_target])
        vlan_termination = L2VPNTermination.objects.create(l2vpn=l2vpn, assigned_object=vlan)
        interface_termination = L2VPNTermination.objects.create(l2vpn=l2vpn, assigned_object=interface)
        vm_interface_termination = L2VPNTermination.objects.create(l2vpn=l2vpn, assigned_object=vm_interface)

        objects = (
            ike_proposal,
            ike_policy,
            ipsec_proposal,
            ipsec_policy,
            ipsec_profile,
            tunnel_group,
            tunnel,
            physical_termination,
            virtual_termination,
            l2vpn,
            vlan_termination,
            interface_termination,
            vm_interface_termination,
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
        ike_policy_record = next(record for record in canonical if record.resource_kind == "ike_policy")
        assert "source-secret" not in str(ike_policy_record.payload)
        assert "/preshared_key" not in ike_policy_record.attributes
        assert {
            next(name for name in record.relationships if name.startswith("termination_"))
            for record in canonical
            if record.resource_kind == "tunnel_termination"
        } == {"termination_interface", "termination_vm_interface"}
        assert {
            next(name for name in record.relationships if name.startswith("assigned_"))
            for record in canonical
            if record.resource_kind == "l2vpn_termination"
        } == {"assigned_vlan", "assigned_interface", "assigned_vm_interface"}

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

        recreated_ike_policy = object_cache[ike_policy_record.resource_kind, ike_policy_record.identity_key]
        assert recreated_ike_policy.preshared_key == ""
        recreated_ike_policy.preshared_key = "destination-secret"
        recreated_ike_policy.save()
        _write_object(
            recreated_ike_policy,
            next(record for record in application_records if record.resource_kind == "ike_policy"),
            target_by_key,
            object_cache,
            references,
        )
        recreated_ike_policy.refresh_from_db()
        assert recreated_ike_policy.preshared_key == "destination-secret"

    def test_contact_assignments_preserve_vpn_targets(self) -> None:
        suffix = uuid4().hex[:8]
        group = TunnelGroup.objects.create(name=f"VPN contacts {suffix}", slug=f"vpn-contacts-{suffix}")
        tunnel = Tunnel.objects.create(name=f"VPN contacts {suffix}", group=group, status="active", encapsulation="gre")
        l2vpn = L2VPN.objects.create(
            name=f"VPN contacts {suffix}", slug=f"vpn-contacts-{suffix}", type="vpls", status="active"
        )
        contact = Contact.objects.create(name=f"VPN operator {suffix}")
        role = ContactRole.objects.create(name=f"VPN technical {suffix}", slug=f"vpn-technical-{suffix}")
        assignments = tuple(
            ContactAssignment.objects.create(object=target, contact=contact, role=role)
            for target in (group, tunnel, l2vpn)
        )

        source_pks = {(obj._meta.label_lower, str(obj.pk)) for obj in assignments}
        records = [
            record
            for record in load_netbox_target_records()
            if (record.target_object_type, record.target_object_id) in source_pks
        ]
        assert {next(name for name in record.relationships if name.startswith("object_")) for record in records} == {
            "object_tunnel_group",
            "object_tunnel",
            "object_l2vpn",
        }
