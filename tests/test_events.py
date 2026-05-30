"""Unit tests for typed event parsing."""

from __future__ import annotations

from datetime import datetime, timezone

from pydigitalstrom import (
    ButtonClickEvent,
    CallSceneEvent,
    ClickType,
    DeviceBinaryInputEvent,
    DssEvent,
    HighLevelEvent,
    SensorType,
    StateChangeEvent,
    ZoneSensorErrorEvent,
    ZoneSensorValueEvent,
    parse_event,
)


def event_raw(
    name: str,
    properties: dict[str, str] | None = None,
    source: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "properties": properties or {},
        "source": source or {
            "set": ".zone(0).group(0)",
            "zoneID": 0,
            "groupID": 0,
            "isApartment": False,
            "isGroup": True,
            "isDevice": False,
        },
    }


class TestEventFactory:
    def test_call_scene_dispatch(self) -> None:
        ev = parse_event(event_raw(
            "callScene",
            {"zoneID": "5", "groupID": "1", "sceneID": "5", "callOrigin": "9"},
        ))
        assert isinstance(ev, CallSceneEvent)
        assert ev.zone_id == 5
        assert ev.group_id == 1
        assert ev.scene_id == 5
        assert ev.call_origin == 9

    def test_button_click_uses_source_dsid(self) -> None:
        ev = parse_event(event_raw(
            "buttonClick",
            {"buttonIndex": "0", "clickType": "0"},
            source={
                "set": "dsid(303505d7f80c8b30)",
                "zoneID": 5,
                "groupID": 0,
                "dsid": "303505d7f80c8b30",
                "isApartment": False,
                "isGroup": False,
                "isDevice": True,
            },
        ))
        assert isinstance(ev, ButtonClickEvent)
        assert ev.button_index == 0
        assert ev.click_type == 0
        assert ev.click_type_enum is ClickType.SINGLE
        assert ev.dsid == "303505d7f80c8b30"

    def test_state_change(self) -> None:
        ev = parse_event(event_raw(
            "stateChange",
            {
                "statename": "zone.5.light",
                "state": "inactive",
                "value": "2",
                "oldvalue": "1",
                "callOrigin": "9",
            },
        ))
        assert isinstance(ev, StateChangeEvent)
        assert ev.state_name == "zone.5.light"
        assert ev.state == "inactive"
        assert ev.value == "2"
        assert ev.old_value == "1"
        assert ev.call_origin == 9

    def test_state_change_missing_call_origin(self) -> None:
        ev = parse_event(event_raw(
            "stateChange",
            {"statename": "x", "state": "active", "value": "1", "oldvalue": "0"},
        ))
        assert isinstance(ev, StateChangeEvent)
        assert ev.call_origin is None

    def test_device_binary_input(self) -> None:
        ev = parse_event(event_raw(
            "deviceBinaryInputEvent",
            {"inputState": "1", "inputIndex": "0", "inputType": "1"},
            source={
                "set": "dsid(abc)",
                "zoneID": 9,
                "groupID": 0,
                "dsid": "abc",
                "isApartment": False,
                "isGroup": False,
                "isDevice": True,
            },
        ))
        assert isinstance(ev, DeviceBinaryInputEvent)
        assert ev.input_state == 1
        assert ev.input_type == 1
        assert ev.dsid == "abc"

    def test_zone_sensor_value(self) -> None:
        ev = parse_event(event_raw(
            "zoneSensorValue",
            {
                "originDSID": "0" * 34,
                "sensorValueFloat": "-11.9375",
                "sensorType": "77",
                "sensorValue": "1249",
            },
            source={
                "set": ".zone(0).group(0)",
                "zoneID": 0,
                "groupID": 0,
                "isApartment": False,
                "isGroup": True,
                "isDevice": False,
            },
        ))
        assert isinstance(ev, ZoneSensorValueEvent)
        assert ev.sensor_type is SensorType.OUTDOOR_TEMP
        assert ev.sensor_type_raw == 77
        assert ev.sensor_value == 1249
        assert ev.sensor_value_float == -11.9375
        assert ev.origin_dsid.startswith("0")

    def test_zone_sensor_error_epoch(self) -> None:
        ev = parse_event(event_raw(
            "zoneSensorError",
            {"lastValueTS": "1970-01-01T00:00:00.000Z", "sensorType": "9"},
            source={
                "set": ".zone(3).group(0)",
                "zoneID": 3,
                "groupID": 0,
                "isApartment": False,
                "isGroup": True,
                "isDevice": False,
            },
        ))
        assert isinstance(ev, ZoneSensorErrorEvent)
        assert ev.sensor_type_raw == 9
        # 1970 epoch - parsable but the value is the epoch itself
        ts = ev.last_value_ts
        assert ts is not None
        assert ts.year == 1970

    def test_zone_sensor_error_missing_ts(self) -> None:
        ev = parse_event(event_raw(
            "zoneSensorError",
            {"sensorType": "9"},
        ))
        assert isinstance(ev, ZoneSensorErrorEvent)
        assert ev.last_value_ts is None

    def test_high_level_event(self) -> None:
        ev = parse_event(event_raw(
            "highlevelevent",
            {"source-name": "Wecker ausführen", "id": "1626014704"},
        ))
        assert isinstance(ev, HighLevelEvent)
        assert ev.action_id == "1626014704"
        assert ev.source_name == "Wecker ausführen"

    def test_unknown_event_type_falls_back(self) -> None:
        ev = parse_event(event_raw("absolutely-unknown-event"))
        assert type(ev) is DssEvent
        assert ev.name == "absolutely-unknown-event"

    def test_received_at_default_is_now_utc(self) -> None:
        before = datetime.now(timezone.utc)
        ev = parse_event(event_raw("callScene"))
        after = datetime.now(timezone.utc)
        assert before <= ev.received_at <= after

    def test_int_prop_invalid_value(self) -> None:
        ev = parse_event(event_raw(
            "callScene",
            {"zoneID": "not-a-number"},
        ))
        assert isinstance(ev, CallSceneEvent)
        assert ev.zone_id == 0  # default fallback
