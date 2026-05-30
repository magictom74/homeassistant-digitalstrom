"""Unit tests for the dataclass + enum models."""

from __future__ import annotations

import pytest

from pydigitalstrom import (
    GROUP_LIGHT,
    GROUP_SHADE,
    SYSTEM_ZONE_IDS,
    Apartment,
    ClickType,
    CombinedCondition,
    Device,
    DeviceCapabilities,
    EventSource,
    Group,
    OutputMode,
    SensorType,
    Zone,
    ZoneSensorStateConfig,
)
from pydigitalstrom.models import StandardScene


def make_device(
    *,
    dsuid: str = "303505d7f800000000001680000c8b3000",
    zone_id: int = 5,
    name: str = "Sample",
    groups: tuple[int, ...] = (1,),
) -> Device:
    return Device(
        dsuid=dsuid,
        dsid="303505d7f80c8b30",
        display_id="00c8b300",
        name=name,
        zone_id=zone_id,
        hw_info="",
        function_id=0,
        product_id=0,
        revision_id=0,
        model=None,
        gtin=None,
        is_present=True,
        is_valid=True,
        last_discovered=None,
        first_seen=None,
        output_mode=OutputMode.SWITCHED,
        output_channels=(),
        button_input_mode=None,
        button_input_index=None,
        button_id=None,
        groups=groups,
        capabilities=DeviceCapabilities(
            has_output=True,
            has_input=False,
            has_sensors=False,
            has_binary_inputs=False,
        ),
        sensors=(),
        binary_inputs=(),
        meter_dsuid=None,
    )


class TestEnums:
    def test_output_mode_known(self) -> None:
        assert OutputMode.from_raw(16) is OutputMode.SWITCHED

    def test_output_mode_unknown(self) -> None:
        assert OutputMode.from_raw(99999) is OutputMode.UNKNOWN

    def test_output_mode_none(self) -> None:
        assert OutputMode.from_raw(None) is OutputMode.UNKNOWN

    def test_sensor_type_known(self) -> None:
        assert SensorType.from_raw(9) is SensorType.TEMP_ROOM
        assert SensorType.from_raw("76") is SensorType.BRIGHTNESS_ROOM

    def test_sensor_type_unknown(self) -> None:
        assert SensorType.from_raw("not-a-number") is SensorType.UNKNOWN

    def test_click_type_known(self) -> None:
        assert ClickType.from_raw(0) is ClickType.SINGLE
        assert ClickType.from_raw("3") is ClickType.HOLD

    def test_click_type_unknown(self) -> None:
        assert ClickType.from_raw(None) is ClickType.UNKNOWN

    def test_standard_scenes(self) -> None:
        assert StandardScene.PRESET_0 == 0
        assert StandardScene.PRESET_1 == 5


class TestEventSource:
    def test_parses_group_source(self) -> None:
        src = EventSource.from_raw(
            {
                "set": ".zone(5).group(2)",
                "groupID": 2,
                "zoneID": 5,
                "isApartment": False,
                "isGroup": True,
                "isDevice": False,
            }
        )
        assert src.kind == "group"
        assert src.zone_id == 5
        assert src.group_id == 2
        assert src.dsid is None

    def test_parses_device_source(self) -> None:
        src = EventSource.from_raw(
            {
                "set": "dsid(303505d7f80c8b30)",
                "groupID": 0,
                "zoneID": 5,
                "dsid": "303505d7f80c8b30",
                "isApartment": False,
                "isGroup": False,
                "isDevice": True,
            }
        )
        assert src.kind == "device"
        assert src.dsid == "303505d7f80c8b30"

    def test_parses_apartment_source(self) -> None:
        src = EventSource.from_raw(
            {
                "set": "",
                "groupID": 0,
                "zoneID": 0,
                "isApartment": True,
                "isGroup": False,
                "isDevice": False,
            }
        )
        assert src.kind == "apartment"

    def test_unknown_source(self) -> None:
        src = EventSource.from_raw({"set": ""})
        assert src.kind == "unknown"


class TestGroup:
    def test_light_predicate(self) -> None:
        g = Group(
            group_id=GROUP_LIGHT,
            name="Licht",
            color=1,
            application_type=1,
            is_present=True,
            device_dsuids=(),
        )
        assert g.is_light
        assert not g.is_shade
        assert not g.is_user_group

    def test_shade_predicate(self) -> None:
        g = Group(
            group_id=GROUP_SHADE,
            name="Jalousie",
            color=2,
            application_type=2,
            is_present=True,
            device_dsuids=(),
        )
        assert g.is_shade
        assert not g.is_light

    def test_user_group(self) -> None:
        g = Group(
            group_id=16,
            name="Wind",
            color=2,
            application_type=2,
            is_present=True,
            device_dsuids=(),
        )
        assert g.is_user_group


class TestZone:
    def test_is_system_zone_by_id(self) -> None:
        z = Zone(zone_id=0, name="", devices=(), groups=())
        assert z.is_system_zone
        for system_id in SYSTEM_ZONE_IDS:
            z2 = Zone(zone_id=system_id, name="anything", devices=(), groups=())
            assert z2.is_system_zone

    def test_is_user_zone(self) -> None:
        z = Zone(zone_id=5, name="Arbeiten", devices=(), groups=())
        assert not z.is_system_zone

    def test_get_group(self) -> None:
        g1 = Group(
            group_id=1, name="L", color=1, application_type=1,
            is_present=True, device_dsuids=(),
        )
        z = Zone(zone_id=5, name="Z", devices=(), groups=(g1,))
        assert z.get_group(1) is g1
        assert z.get_group(99) is None
        assert z.light_group is g1
        assert z.shade_group is None

    def test_get_device(self) -> None:
        d = make_device()
        z = Zone(zone_id=5, name="Z", devices=(d,), groups=())
        assert z.get_device(d.dsuid) is d
        assert z.get_device("not-there") is None


class TestApartment:
    def test_get_zone(self) -> None:
        zones = (
            Zone(zone_id=0, name="", devices=(), groups=()),
            Zone(zone_id=5, name="Arbeiten", devices=(), groups=()),
        )
        apt = Apartment(
            name="X", dsid="d", dsuid="du", zones=zones,
            clusters=(), circuits=(),
        )
        assert apt.get_zone(5) is zones[1]
        assert apt.get_zone(999) is None
        assert apt.find_zone_by_name("Arbeiten") is zones[1]
        assert apt.find_zone_by_name("arbeiten") is zones[1]  # case-insensitive

    def test_user_zones_excludes_system(self) -> None:
        zones = (
            Zone(zone_id=0, name="", devices=(), groups=()),
            Zone(zone_id=5, name="Arbeiten", devices=(), groups=()),
        )
        apt = Apartment(
            name="X", dsid="d", dsuid="du", zones=zones,
            clusters=(), circuits=(),
        )
        user_zones = apt.user_zones
        assert len(user_zones) == 1
        assert user_zones[0].zone_id == 5

    def test_all_devices_deduplicates(self) -> None:
        # Same device appearing in two zones should appear once in all_devices
        d1 = make_device(name="d1")
        d2 = make_device(dsuid="303505d7f80000000000168000999900", name="d2")
        zone_a = Zone(zone_id=5, name="A", devices=(d1, d2), groups=())
        zone_b = Zone(zone_id=6, name="B", devices=(d1,), groups=())
        apt = Apartment(
            name="X", dsid="d", dsuid="du", zones=(zone_a, zone_b),
            clusters=(), circuits=(),
        )
        all_devs = apt.all_devices
        assert len(all_devs) == 2
        dsuids = {d.dsuid for d in all_devs}
        assert dsuids == {d1.dsuid, d2.dsuid}

    def test_get_device_by_dsid(self) -> None:
        d = make_device()
        z = Zone(zone_id=5, name="Z", devices=(d,), groups=())
        apt = Apartment(
            name="X", dsid="d", dsuid="du", zones=(z,),
            clusters=(), circuits=(),
        )
        assert apt.get_device(d.dsuid) is d
        assert apt.get_device_by_dsid(d.dsid) is d
        assert apt.get_device("not-there") is None


class TestCombinedCondition:
    def test_for_user_state_factory(self) -> None:
        c = CombinedCondition.for_user_state("1498673719", value="inactive")
        assert c.state == "1498673719"
        assert c.value == "inactive"
        assert c.addon_id == "system-addon-user-defined-states"

    def test_for_system_state_factory(self) -> None:
        c = CombinedCondition.for_system_state("zone.5.light", "inactive")
        assert c.addon_id == ""
        assert c.state == "zone.5.light"


class TestZoneSensorStateConfig:
    def test_basic_fields(self) -> None:
        cfg = ZoneSensorStateConfig(
            zone_id=7, group_id=48, type_id=51,
            active_value=50.0, inactive_value=45.0,
            greater_on_set=True,
        )
        assert cfg.zone_id == 7
        assert cfg.greater_on_set


class TestImmutability:
    def test_event_source_frozen(self) -> None:
        src = EventSource.from_raw({"set": ""})
        with pytest.raises(Exception):  # FrozenInstanceError or dataclasses.FrozenInstanceError
            src.zone_id = 999  # type: ignore[misc]

    def test_zone_frozen(self) -> None:
        z = Zone(zone_id=5, name="Z", devices=(), groups=())
        with pytest.raises(Exception):
            z.name = "Other"  # type: ignore[misc]
