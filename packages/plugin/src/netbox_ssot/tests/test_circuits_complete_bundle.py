from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

from circuits.models import (
    Circuit,
    CircuitGroup,
    CircuitGroupAssignment,
    CircuitTermination,
    CircuitType,
    Provider,
    ProviderAccount,
    ProviderNetwork,
    VirtualCircuit,
    VirtualCircuitTermination,
    VirtualCircuitType,
)
from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, Location, Manufacturer, Site
from django.apps import apps
from django.test import TestCase
from extras.models import Tag
from ipam.models import ASN, RIR
from tenancy.models import Tenant

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import (
    _resolve_external_references,
    _write_deferred_relationships,
    _write_object,
)
from netbox_ssot.planning.circuits import CIRCUITS_RESOURCE_KINDS
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records


class CircuitsCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_every_public_circuits_resource_round_trips_through_snapshot_and_writer(self) -> None:
        suffix = uuid4().hex[:8]
        tag = Tag.objects.create(name=f"Circuit {suffix}", slug=f"circuit-{suffix}", color="00aa00")
        tenant = Tenant.objects.create(name=f"Circuit {suffix}", slug=f"circuit-{suffix}")
        rir = RIR.objects.create(name=f"Circuit {suffix}", slug=f"circuit-{suffix}")
        asn = ASN.objects.create(asn=64512, rir=rir)
        site = Site.objects.create(name=f"Circuit {suffix}", slug=f"circuit-{suffix}")
        location = Location.objects.create(name="Meet me room", slug=f"mmr-{suffix}", site=site)

        manufacturer = Manufacturer.objects.create(name=f"Circuit {suffix}", slug=f"circuit-{suffix}")
        role = DeviceRole.objects.create(name=f"Circuit {suffix}", slug=f"circuit-{suffix}", color="2196f3")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model=f"Circuit {suffix}")
        device = Device.objects.create(
            name=f"circuit-{suffix}", device_type=device_type, role=role, site=site, status="active"
        )
        physical_interface = Interface.objects.create(device=device, name="eth0", type="1000base-t")
        virtual_interface = Interface.objects.create(device=device, name="eth0.100", type="virtual")

        provider = Provider.objects.create(
            name=f"Provider {suffix}", slug=f"provider-{suffix}", description="Carrier", comments="Provider notes"
        )
        provider.asns.add(asn)
        account = ProviderAccount.objects.create(
            provider=provider,
            account=f"ACCT-{suffix}",
            name="Production",
            description="Billing account",
            comments="Account notes",
        )
        network = ProviderNetwork.objects.create(
            provider=provider,
            name=f"MPLS {suffix}",
            service_id=f"SVC-{suffix}",
            description="Provider backbone",
            comments="Network notes",
        )
        circuit_type = CircuitType.objects.create(
            name=f"Transit {suffix}", slug=f"transit-{suffix}", color="112233", description="Transit"
        )
        virtual_type = VirtualCircuitType.objects.create(
            name=f"EVPN {suffix}", slug=f"evpn-{suffix}", color="445566", description="EVPN"
        )
        group = CircuitGroup.objects.create(
            name=f"WAN {suffix}", slug=f"wan-{suffix}", tenant=tenant, description="WAN group"
        )
        circuit = Circuit.objects.create(
            cid=f"CID-{suffix}",
            provider=provider,
            provider_account=account,
            type=circuit_type,
            status="active",
            tenant=tenant,
            install_date=date(2026, 1, 2),
            termination_date=date(2028, 1, 2),
            commit_rate=1_000_000,
            distance=Decimal("12.50"),
            distance_unit="km",
            description="Primary circuit",
            comments="Circuit notes",
        )
        termination_a = CircuitTermination.objects.create(
            circuit=circuit,
            term_side="A",
            termination=location,
            port_speed=1_000_000,
            upstream_speed=500_000,
            xconnect_id="XC-A",
            pp_info="PP1/1",
            description="Local handoff",
        )
        termination_z = CircuitTermination.objects.create(
            circuit=circuit,
            term_side="Z",
            termination=network,
            port_speed=1_000_000,
            description="Provider handoff",
            mark_connected=True,
        )
        virtual_circuit = VirtualCircuit.objects.create(
            cid=f"VC-{suffix}",
            provider_network=network,
            provider_account=account,
            type=virtual_type,
            status="active",
            tenant=tenant,
            description="Virtual service",
            comments="Virtual notes",
        )
        virtual_termination = VirtualCircuitTermination.objects.create(
            virtual_circuit=virtual_circuit,
            role="peer",
            interface=virtual_interface,
            description="Customer edge",
        )
        circuit_assignment = CircuitGroupAssignment.objects.create(
            group=group, member=circuit, priority="primary"
        )
        virtual_assignment = CircuitGroupAssignment.objects.create(
            group=group, member=virtual_circuit, priority="secondary"
        )
        cable = Cable(
            a_terminations=[termination_a],
            b_terminations=[physical_interface],
            status="connected",
            label=f"Circuit handoff {suffix}",
        )
        cable.full_clean()
        cable.save()

        circuit_objects = [
            provider,
            account,
            network,
            circuit_type,
            virtual_type,
            group,
            circuit,
            termination_a,
            termination_z,
            virtual_circuit,
            virtual_termination,
            circuit_assignment,
            virtual_assignment,
            cable,
        ]
        for obj in circuit_objects:
            obj.tags.add(tag)

        selected = {
            (kind, str(obj.pk))
            for kind, objects in {
                "provider": [provider],
                "provider_account": [account],
                "provider_network": [network],
                "circuit_type": [circuit_type],
                "virtual_circuit_type": [virtual_type],
                "circuit_group": [group],
                "circuit": [circuit],
                "circuit_termination": [termination_a, termination_z],
                "virtual_circuit": [virtual_circuit],
                "virtual_circuit_termination": [virtual_termination],
                "circuit_group_assignment": [circuit_assignment, virtual_assignment],
                "cable": [cable],
            }.items()
            for obj in objects
        }
        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.resource_kind, record.target_object_id) in selected
        ]
        assert {record.resource_kind for record in canonical} == CIRCUITS_RESOURCE_KINDS | {"cable"}
        provider_record = next(record for record in canonical if record.resource_kind == "provider")
        assert provider_record.relationships["asn"]
        termination_records = [record for record in canonical if record.resource_kind == "circuit_termination"]
        termination_relationships = {
            name for record in termination_records for name in record.relationships if name.startswith("termination_")
        }
        assert termination_relationships == {
            "termination_location",
            "termination_provider_network",
        }
        assignment_records = [record for record in canonical if record.resource_kind == "circuit_group_assignment"]
        member_relationships = {
            name for record in assignment_records for name in record.relationships if name.startswith("member_")
        }
        assert member_relationships == {
            "member_circuit",
            "member_virtual_circuit",
        }

        expected = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        records = [
            ApplicationRecord(record.resource_kind, record.identity_key, record.attributes, record.relationships)
            for record in canonical
        ]

        cable.delete()
        CircuitGroupAssignment.objects.filter(pk__in=[circuit_assignment.pk, virtual_assignment.pk]).delete()
        virtual_termination.delete()
        virtual_circuit.delete()
        termination_a.delete()
        termination_z.delete()
        circuit.delete()
        group.delete()
        virtual_type.delete()
        circuit_type.delete()
        account.delete()
        network.delete()
        provider.delete()

        target_records = load_netbox_target_records()
        target_by_key = {(record.resource_kind, record.identity_key): record for record in target_records}
        references, problems = _resolve_external_references(records)
        assert problems == ()
        object_cache: dict[tuple[str, str], object] = {}
        ordered = dependency_order(records)
        for record in ordered:
            obj = MODEL_BY_KIND[record.resource_kind]()
            _write_object(obj, record, target_by_key, object_cache, references)
            object_cache[record.key] = obj
        _write_deferred_relationships(ordered, target_by_key, object_cache)

        recreated = {
            (record.resource_kind, record.identity_key): record.payload
            for record in load_netbox_target_records()
            if (record.resource_kind, record.identity_key) in expected
        }
        assert recreated == expected
        assert Interface.objects.get(pk=physical_interface.pk).name == "eth0"
