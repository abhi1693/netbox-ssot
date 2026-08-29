from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from dcim.models import (
    Cable,
    CableBundle,
    ConsolePort,
    ConsolePortTemplate,
    ConsoleServerPort,
    ConsoleServerPortTemplate,
    Device,
    DeviceBay,
    DeviceBayTemplate,
    DeviceRole,
    DeviceType,
    FrontPort,
    FrontPortTemplate,
    Interface,
    InterfaceTemplate,
    InventoryItem,
    InventoryItemRole,
    InventoryItemTemplate,
    MACAddress,
    Manufacturer,
    Module,
    ModuleBay,
    ModuleBayTemplate,
    ModuleType,
    ModuleTypeProfile,
    PortTemplateMapping,
    PowerFeed,
    PowerOutlet,
    PowerOutletTemplate,
    PowerPanel,
    PowerPort,
    PowerPortTemplate,
    Rack,
    RackReservation,
    RearPort,
    RearPortTemplate,
    Site,
    VirtualChassis,
    VirtualDeviceContext,
)
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from netbox_ssot.application.planning import ApplicationRecord, dependency_order
from netbox_ssot.application.service import (
    _resolve_external_references,
    _write_deferred_relationships,
    _write_object,
)
from netbox_ssot.planning.comparison import SUPPORTED_RESOURCE_KINDS
from netbox_ssot.planning.dcim import DCIM_RESOURCE_KINDS
from netbox_ssot.planning.netbox_target import MODEL_BY_KIND, load_netbox_target_records
from netbox_ssot.planning.resource_registry import ATTRIBUTE_FIELDS, RELATIONSHIP_FIELDS


class DCIMCompleteBundleTests(TestCase):
    available_apps: ClassVar[list[str]] = [app_config.name for app_config in apps.get_app_configs()]

    def test_declarative_registry_covers_every_supported_target_model(self) -> None:
        expected = set(SUPPORTED_RESOURCE_KINDS)

        assert set(MODEL_BY_KIND) == expected
        assert set(ATTRIBUTE_FIELDS) == expected
        assert set(RELATIONSHIP_FIELDS) == expected

    def test_rack_reservation_creates_its_missing_user_dependency(self) -> None:
        suffix = uuid4().hex[:8]
        user_model = get_user_model()
        user = user_model.objects.create_user(username=f"reservation-{suffix}")
        site = Site.objects.create(name=f"Reservation {suffix}", slug=f"reservation-{suffix}")
        rack = Rack.objects.create(name=f"Reservation {suffix}", site=site, width=19, u_height=42)
        reservation = RackReservation.objects.create(
            rack=rack,
            units=[10],
            status="active",
            user=user,
            description="Reserved for source user",
        )

        source_records = [
            record
            for record in load_netbox_target_records()
            if (record.resource_kind, record.target_object_id)
            in {("user", str(user.pk)), ("rack_reservation", str(reservation.pk))}
        ]
        reservation.delete()
        user.delete()

        target_records = load_netbox_target_records()
        target_by_key = {(record.resource_kind, record.identity_key): record for record in target_records}
        records = [
            ApplicationRecord(record.resource_kind, record.identity_key, record.attributes, record.relationships)
            for record in source_records
        ]
        ordered = dependency_order(records)
        assert [record.resource_kind for record in ordered] == ["user", "rack_reservation"]

        object_cache: dict[tuple[str, str], object] = {}
        for record in ordered:
            obj = MODEL_BY_KIND[record.resource_kind]()
            _write_object(obj, record, target_by_key, object_cache, {})
            object_cache[record.key] = obj

        recreated_user = user_model.objects.get(username=f"reservation-{suffix}")
        recreated_reservation = RackReservation.objects.get(rack=rack, units=[10])
        assert not recreated_user.has_usable_password()
        assert recreated_reservation.user == recreated_user

    def test_every_public_dcim_resource_round_trips_through_snapshot_and_writer(self) -> None:
        suffix = uuid4().hex[:8]
        user = get_user_model().objects.create_user(username=f"dcim-{suffix}")
        site = Site.objects.create(name=f"DCIM {suffix}", slug=f"dcim-{suffix}")
        manufacturer = Manufacturer.objects.create(name=f"DCIM {suffix}", slug=f"dcim-{suffix}")
        role = DeviceRole.objects.create(name=f"DCIM {suffix}", slug=f"dcim-{suffix}", color="2196f3")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model=f"DCIM {suffix}",
            slug=f"dcim-{suffix}",
            u_height=1,
            subdevice_role="parent",
        )
        rack = Rack.objects.create(name=f"DCIM {suffix}", site=site, width=19, u_height=42)

        profile = ModuleTypeProfile.objects.create(
            name=f"DCIM {suffix}",
            schema={"type": "object", "properties": {"slot": {"type": "string"}}},
        )
        module_type = ModuleType.objects.create(
            profile=profile,
            manufacturer=manufacturer,
            model=f"Module {suffix}",
            attribute_data={"slot": "A"},
        )
        inventory_role = InventoryItemRole.objects.create(
            name=f"Inventory {suffix}", slug=f"inventory-{suffix}", color="4caf50"
        )

        ConsolePortTemplate.objects.create(device_type=device_type, name="Console", type="rj-45")
        ConsoleServerPortTemplate.objects.create(device_type=device_type, name="Console server", type="rj-45")
        PowerPortTemplate.objects.create(device_type=device_type, name="Power in", type="iec-60320-c14")
        PowerOutletTemplate.objects.create(device_type=device_type, name="Power out", type="iec-60320-c13")
        InterfaceTemplate.objects.create(device_type=device_type, name="eth0", type="1000base-t")
        InterfaceTemplate.objects.create(device_type=device_type, name="eth1", type="1000base-t")
        rear_template = RearPortTemplate.objects.create(device_type=device_type, name="Rear", type="8p8c", positions=1)
        front_template = FrontPortTemplate.objects.create(
            device_type=device_type, name="Front", type="8p8c", positions=1
        )
        PortTemplateMapping.objects.create(
            front_port=front_template,
            rear_port=rear_template,
            front_port_position=1,
            rear_port_position=1,
        )
        ModuleBayTemplate.objects.create(device_type=device_type, name="Module bay", enabled=True)
        DeviceBayTemplate.objects.create(device_type=device_type, name="Device bay", enabled=True)
        InventoryItemTemplate.objects.create(
            device_type=device_type,
            name="Transceiver",
            role=inventory_role,
            manufacturer=manufacturer,
            component=InterfaceTemplate.objects.get(device_type=device_type, name="eth0"),
        )

        chassis = VirtualChassis.objects.create(name=f"VC {suffix}")
        device = Device.objects.create(
            name=f"device-{suffix}",
            device_type=device_type,
            role=role,
            site=site,
            rack=rack,
            position=1,
            face="front",
            status="active",
            virtual_chassis=chassis,
            vc_position=1,
            local_context_data={"purpose": "round-trip"},
        )
        chassis.master = device
        chassis.save()
        vdc = VirtualDeviceContext.objects.create(device=device, name="default", status="active")
        module_bay = ModuleBay.objects.get(device=device, name="Module bay")
        Module.objects.create(device=device, module_bay=module_bay, module_type=module_type, status="active")
        interface = Interface.objects.get(device=device, name="eth0")
        peer_interface = Interface.objects.get(device=device, name="eth1")
        interface.vdcs.add(vdc)
        mac = MACAddress.objects.create(mac_address="00:11:22:33:44:55", assigned_object=interface)
        interface.primary_mac_address = mac
        interface.save()
        reservation = RackReservation.objects.create(
            rack=rack,
            units=[10, 11],
            status="active",
            user=user,
            description="Reserved",
        )
        panel = PowerPanel.objects.create(site=site, name=f"Panel {suffix}")
        feed = PowerFeed.objects.create(power_panel=panel, rack=rack, name=f"Feed {suffix}")
        bundle = CableBundle.objects.create(name=f"Bundle {suffix}")
        cable = Cable(
            a_terminations=[interface],
            b_terminations=[peer_interface],
            status="connected",
            bundle=bundle,
            label=f"Cable {suffix}",
        )
        cable.full_clean()
        cable.save()

        selected: set[tuple[str, str]] = {
            ("module_type_profile", str(profile.pk)),
            ("module_type", str(module_type.pk)),
            ("inventory_item_role", str(inventory_role.pk)),
            ("virtual_chassis", str(chassis.pk)),
            ("device", str(device.pk)),
            ("virtual_device_context", str(vdc.pk)),
            ("mac_address", str(mac.pk)),
            ("rack_reservation", str(reservation.pk)),
            ("power_panel", str(panel.pk)),
            ("power_feed", str(feed.pk)),
            ("cable_bundle", str(bundle.pk)),
            ("cable", str(cable.pk)),
        }
        component_models = {
            "console_port_template": ConsolePortTemplate,
            "console_server_port_template": ConsoleServerPortTemplate,
            "power_port_template": PowerPortTemplate,
            "power_outlet_template": PowerOutletTemplate,
            "interface_template": InterfaceTemplate,
            "rear_port_template": RearPortTemplate,
            "front_port_template": FrontPortTemplate,
            "module_bay_template": ModuleBayTemplate,
            "device_bay_template": DeviceBayTemplate,
            "inventory_item_template": InventoryItemTemplate,
            "module_bay": ModuleBay,
            "device_bay": DeviceBay,
            "module": Module,
            "console_port": ConsolePort,
            "console_server_port": ConsoleServerPort,
            "power_port": PowerPort,
            "power_outlet": PowerOutlet,
            "interface": Interface,
            "rear_port": RearPort,
            "front_port": FrontPort,
            "inventory_item": InventoryItem,
        }
        for kind, model in component_models.items():
            queryset = (
                model.objects.filter(device_type=device_type)
                if kind.endswith("_template")
                else model.objects.filter(device=device)
            )
            selected.update((kind, str(pk)) for pk in queryset.values_list("pk", flat=True))

        canonical = [
            record
            for record in load_netbox_target_records()
            if (record.resource_kind, record.target_object_id) in selected
        ]
        assert {record.resource_kind for record in canonical} == DCIM_RESOURCE_KINDS - {
            "manufacturer",
            "device_role",
            "platform",
            "device_type",
            "rack_group",
            "rack_role",
            "rack_type",
            "rack",
        }
        expected = {(record.resource_kind, record.identity_key): record.payload for record in canonical}
        reservation_record = next(record for record in canonical if record.resource_kind == "rack_reservation")
        assert "/user" not in reservation_record.attributes
        assert reservation_record.relationships["user"] == next(
            record.identity_key
            for record in load_netbox_target_records(datasets=("users",))
            if record.resource_kind == "user" and record.target_object_id == str(user.pk)
        )
        records = [
            ApplicationRecord(record.resource_kind, record.identity_key, record.attributes, record.relationships)
            for record in canonical
        ]

        cable.delete()
        interface.primary_mac_address = None
        interface.save()
        mac.delete()
        reservation.delete()
        feed.delete()
        panel.delete()
        InventoryItem.objects.filter(device=device).delete()
        Module.objects.filter(device=device).delete()
        VirtualDeviceContext.objects.filter(device=device).delete()
        PowerOutlet.objects.filter(device=device).delete()
        PowerPort.objects.filter(device=device).delete()
        FrontPort.objects.filter(device=device).delete()
        RearPort.objects.filter(device=device).delete()
        Interface.objects.filter(device=device).delete()
        ConsolePort.objects.filter(device=device).delete()
        ConsoleServerPort.objects.filter(device=device).delete()
        DeviceBay.objects.filter(device=device).delete()
        ModuleBay.objects.filter(device=device).delete()
        chassis.master = None
        chassis.save()
        device.delete()
        chassis.delete()
        InventoryItemTemplate.objects.filter(device_type=device_type).delete()
        PowerOutletTemplate.objects.filter(device_type=device_type).delete()
        PowerPortTemplate.objects.filter(device_type=device_type).delete()
        FrontPortTemplate.objects.filter(device_type=device_type).delete()
        RearPortTemplate.objects.filter(device_type=device_type).delete()
        InterfaceTemplate.objects.filter(device_type=device_type).delete()
        ConsolePortTemplate.objects.filter(device_type=device_type).delete()
        ConsoleServerPortTemplate.objects.filter(device_type=device_type).delete()
        DeviceBayTemplate.objects.filter(device_type=device_type).delete()
        ModuleBayTemplate.objects.filter(device_type=device_type).delete()
        module_type.delete()
        profile.delete()
        inventory_role.delete()
        bundle.delete()

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
