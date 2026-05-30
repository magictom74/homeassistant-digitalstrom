"""Data model: immutable frozen dataclasses for all dSS objects.

This module contains every domain type the library exposes. Types are grouped
by concern (apartment, devices, scenes, events, addons). Each dataclass
documents which dSS endpoint produces it in its docstring.

The models are intentionally **frozen** - any modification builds a new
instance. Lists are stored as tuples to preserve immutability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Mapping

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OutputMode(int, Enum):
    """Device ``outputMode`` semantics.

    Values map to dSS standard codes. Unknown raw values become :attr:`UNKNOWN`
    instead of raising - the dSS occasionally introduces new modes.
    """

    DISABLED = 0
    SWITCHED = 16
    DIMMED = 22
    DIMMED_RGB = 35
    POSITION_CONTROL = 33
    POSITION_CONTROL_TILT = 42
    UNKNOWN = -1

    @classmethod
    def from_raw(cls, raw: int | None) -> OutputMode:
        if raw is None:
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


class SensorType(int, Enum):
    """Sensor type codes observed in the wild.

    Tom's box revealed types 9 (room temp), 76 (brightness), 77 (outdoor temp).
    Full list lives in the dSS docs - extend as needed.
    """

    TEMP_ROOM = 9
    BRIGHTNESS_ROOM = 76
    OUTDOOR_TEMP = 77
    UNKNOWN = -1

    @classmethod
    def from_raw(cls, raw: int | str | None) -> SensorType:
        if raw is None:
            return cls.UNKNOWN
        try:
            return cls(int(raw))
        except (ValueError, TypeError):
            return cls.UNKNOWN


class ClickType(int, Enum):
    """``buttonClick.properties.clickType`` values."""

    SINGLE = 0
    DOUBLE = 1
    TRIPLE = 2
    HOLD = 3
    RELEASE = 4
    UNKNOWN = -1

    @classmethod
    def from_raw(cls, raw: int | str | None) -> ClickType:
        if raw is None:
            return cls.UNKNOWN
        try:
            return cls(int(raw))
        except (ValueError, TypeError):
            return cls.UNKNOWN


class StandardScene(int, Enum):
    """Conventional dSS scene numbers (light + shade).

    These are *conventions* per group, not enforced by the bus. Custom devices
    may interpret values differently (e.g. Tom's outdoor sockets use 13/14
    for on/off instead of 5/0).
    """

    PRESET_0 = 0  # Off / position 0
    PRESET_1 = 5  # On / position 1
    PRESET_2 = 17
    PRESET_3 = 18
    PRESET_4 = 19
    LOCAL_ON = 13
    LOCAL_OFF = 14
    DEEP_OFF = 68


# Special zone IDs observed on Tom's box. System zones don't have user-set names.
SYSTEM_ZONE_IDS: frozenset[int] = frozenset({0, 14368, 65534})


# Standard group IDs (per dSS convention).
GROUP_BROADCAST = 0
GROUP_LIGHT = 1
GROUP_SHADE = 2


# ---------------------------------------------------------------------------
# Event source (universal)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventSource:
    """Origin descriptor present on every dSS event.

    The ``set_expr`` field uses dSS' set-notation language - e.g.
    ``.zone(5).group(2)`` or ``dsid(<DSUID>)``. It's the cleanest way to filter
    incoming events to a particular zone/group/device subset.

    Source: ``event.source`` block.
    """

    set_expr: str
    zone_id: int
    group_id: int
    dsid: str | None
    is_apartment: bool
    is_group: bool
    is_device: bool

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> EventSource:
        return cls(
            set_expr=str(raw.get("set", "")),
            zone_id=int(raw.get("zoneID", 0)),
            group_id=int(raw.get("groupID", 0)),
            dsid=raw.get("dsid"),
            is_apartment=bool(raw.get("isApartment", False)),
            is_group=bool(raw.get("isGroup", False)),
            is_device=bool(raw.get("isDevice", False)),
        )

    @property
    def kind(self) -> Literal["apartment", "group", "device", "unknown"]:
        """Discriminator for switch-style event handling."""
        if self.is_device:
            return "device"
        if self.is_group:
            return "group"
        if self.is_apartment:
            return "apartment"
        return "unknown"


# ---------------------------------------------------------------------------
# Apartment structure
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    """Derived booleans summarising what a device can do."""

    has_output: bool
    has_input: bool
    has_sensors: bool
    has_binary_inputs: bool


@dataclass(frozen=True, slots=True)
class OutputChannel:
    """Single output channel of a multi-channel device.

    ``channel_id`` is a semantic identifier string (e.g. ``"brightness"``,
    ``"hue"``, ``"saturation"``) - NOT an integer. ``channel_type`` is often
    the same value, kept separately when present.

    Source: ``device.outputChannels[]``.
    """

    channel_index: int
    channel_id: str
    channel_type: str
    channel_name: str | None


@dataclass(frozen=True, slots=True)
class Sensor:
    """Sensor metadata + last known value.

    Source: ``device.sensors[]``.
    """

    sensor_index: int
    sensor_type: SensorType
    last_value: float | None
    last_value_ts: datetime | None


@dataclass(frozen=True, slots=True)
class BinaryInput:
    """Binary input on a device (motion detector, reed contact, ...).

    Source: ``device.binaryInputs[]``.
    """

    input_index: int
    input_type: int
    target_type: int
    target_group: int
    state: bool | None


@dataclass(frozen=True, slots=True)
class Device:
    """Physical or virtual dSS device.

    Source: ``apartment.zones[].devices[]`` (+ optional ``device/getInfo``
    for richer fields). ``dsuid`` is the modern unique id; ``dsid`` is the
    legacy form used in some event payloads.
    """

    dsuid: str
    dsid: str
    display_id: str
    name: str
    zone_id: int
    hw_info: str
    function_id: int
    product_id: int
    revision_id: int
    model: str | None
    gtin: str | None
    is_present: bool
    is_valid: bool
    last_discovered: str | None
    first_seen: str | None
    output_mode: OutputMode
    output_channels: tuple[OutputChannel, ...]
    button_input_mode: int | None
    button_input_index: int | None
    button_id: int | None
    groups: tuple[int, ...]
    capabilities: DeviceCapabilities
    sensors: tuple[Sensor, ...]
    binary_inputs: tuple[BinaryInput, ...]
    meter_dsuid: str | None


@dataclass(frozen=True, slots=True)
class Group:
    """Logical grouping of devices within a zone.

    Standard groups (1=light yellow, 2=shade grey) are universal; custom
    groups (id > 9 typically) live alongside.

    Source: ``apartment.zones[].groups[]``.
    """

    group_id: int
    name: str
    color: int
    application_type: int
    is_present: bool
    device_dsuids: tuple[str, ...]

    @property
    def is_light(self) -> bool:
        return self.group_id == GROUP_LIGHT

    @property
    def is_shade(self) -> bool:
        return self.group_id == GROUP_SHADE

    @property
    def is_user_group(self) -> bool:
        return self.group_id > 9


@dataclass(frozen=True, slots=True)
class Cluster:
    """Cross-zone cluster (e.g. wind-shield shade group).

    Tom's box has one cluster: id=16, name "Windschutz Jalousien".

    Source: ``apartment.clusters[]``.
    """

    cluster_id: int
    name: str
    color: int
    application_type: int
    is_present: bool


@dataclass(frozen=True, slots=True)
class Circuit:
    """dSM-Meter (bus master).

    Source: ``apartment/getCircuits.circuits[]``.
    """

    name: str
    dsid: str
    dsuid: str
    hw_version: str | None
    sw_version: str | None
    arm_sw_version: str | None
    api_version: str | None
    is_present: bool
    is_valid: bool
    bus_member_type: int | None


@dataclass(frozen=True, slots=True)
class Zone:
    """Logical room or area in the apartment.

    System zones (ids 0, 14368, 65534 on Tom's box) have empty names and are
    used for broadcast / cluster / reserved purposes.

    Source: ``apartment.zones[]``.
    """

    zone_id: int
    name: str
    devices: tuple[Device, ...]
    groups: tuple[Group, ...]

    @property
    def is_system_zone(self) -> bool:
        return self.zone_id in SYSTEM_ZONE_IDS or not self.name

    @property
    def light_group(self) -> Group | None:
        return self.get_group(GROUP_LIGHT)

    @property
    def shade_group(self) -> Group | None:
        return self.get_group(GROUP_SHADE)

    def get_group(self, group_id: int) -> Group | None:
        for g in self.groups:
            if g.group_id == group_id:
                return g
        return None

    def get_device(self, dsuid: str) -> Device | None:
        for d in self.devices:
            if d.dsuid == dsuid:
                return d
        return None


@dataclass(frozen=True, slots=True)
class Apartment:
    """Root of the apartment object tree."""

    name: str
    dsid: str
    dsuid: str
    zones: tuple[Zone, ...]
    clusters: tuple[Cluster, ...]
    circuits: tuple[Circuit, ...]

    def get_zone(self, zone_id: int) -> Zone | None:
        for z in self.zones:
            if z.zone_id == zone_id:
                return z
        return None

    def find_zone_by_name(self, name: str) -> Zone | None:
        norm = name.strip().lower()
        for z in self.zones:
            if z.name.strip().lower() == norm:
                return z
        return None

    def get_device(self, dsuid: str) -> Device | None:
        for z in self.zones:
            d = z.get_device(dsuid)
            if d is not None:
                return d
        return None

    def get_device_by_dsid(self, dsid: str) -> Device | None:
        for z in self.zones:
            for d in z.devices:
                if d.dsid == dsid:
                    return d
        return None

    @property
    def user_zones(self) -> tuple[Zone, ...]:
        """Zones intended for user interaction (excludes system / broadcast zones)."""
        return tuple(z for z in self.zones if not z.is_system_zone)

    @property
    def all_devices(self) -> tuple[Device, ...]:
        """Flat de-duped list of devices across all user zones.

        Devices may be reported in multiple zones (broadcast zone 0 contains
        every device) - we deduplicate by dsuid.
        """
        seen: set[str] = set()
        out: list[Device] = []
        for z in self.user_zones:
            for d in z.devices:
                if d.dsuid not in seen:
                    seen.add(d.dsuid)
                    out.append(d)
        return tuple(out)


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SceneCall:
    """Convenience wrapper for callScene / undoScene parameters."""

    zone_id: int
    group_id: int
    scene_id: int
    force: bool = False

    def to_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "id": self.zone_id,
            "groupID": self.group_id,
            "sceneNumber": self.scene_id,
        }
        if self.force:
            params["force"] = "true"
        return params


# ---------------------------------------------------------------------------
# Addon-specific models (v0.2 - stub-level, full schemas come with addon impls)
# ---------------------------------------------------------------------------


class UserActionType(str, Enum):
    """All action types observed in Tom's /usr/events on 2026-05-28."""

    DEVICE_SCENE = "device-scene"
    ZONE_SCENE = "zone-scene"
    URL = "url"
    CHANGE_ADDON_STATE = "change-addon-state"
    DEVICE_BLINK = "device-blink"
    CUSTOM_EVENT = "custom-event"
    UNDO_ZONE_SCENE = "undo-zone-scene"
    CHANGE_STATE = "change-state"
    ZONE_BLINK = "zone-blink"
    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, raw: str | None) -> UserActionType:
        if not raw:
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class UserActionAction:
    """One action inside a UserAction or TimedEvent.

    The dSS stores action params in a flat dict; type-specific fields
    (``dsuid``, ``zone``, ``url``, ...) are populated only when applicable.
    """

    action_type: UserActionType
    delay: int = 0
    category: str = "manual"
    dsuid: str | None = None
    zone: int | None = None
    group: int | None = None
    scene: int | None = None
    force: bool | None = None
    url: str | None = None
    statename: str | None = None
    state: str | None = None
    addon_id: str | None = None
    event: str | None = None

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> UserActionAction:
        return cls(
            action_type=UserActionType.from_raw(raw.get("type")),
            delay=int(raw.get("delay", 0)),
            category=str(raw.get("category", "manual")),
            dsuid=raw.get("dsuid"),
            zone=raw.get("zone"),
            group=raw.get("group"),
            scene=raw.get("scene"),
            force=raw.get("force"),
            url=raw.get("url"),
            statename=raw.get("statename"),
            state=raw.get("state"),
            addon_id=raw.get("addon-id"),
            event=raw.get("event"),
        )


@dataclass(frozen=True, slots=True)
class UserActionConditions:
    """Conditions that must hold for a UserAction / TimedEvent to fire."""

    enabled: bool = True
    system_states: Mapping[str, str] = field(default_factory=dict)
    addon_states: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UserAction:
    """User-defined action stored under ``/usr/events/<id>``.

    On Tom's box: 69 entries, all with source ``system-addon-user-defined-actions``.
    """

    action_id: str
    name: str
    source: str
    disabled: bool
    last_saved: datetime | None
    last_executed: datetime | None
    conditions: UserActionConditions
    actions: tuple[UserActionAction, ...]

    @property
    def is_user_defined(self) -> bool:
        return self.source == "system-addon-user-defined-actions"


class TimeBase(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    SUNRISE = "sunrise"
    SUNSET = "sunset"
    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, raw: str | None) -> TimeBase:
        if not raw:
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


class Weekday(str, Enum):
    MO = "MO"
    TU = "TU"
    WE = "WE"
    TH = "TH"
    FR = "FR"
    SA = "SA"
    SU = "SU"


@dataclass(frozen=True, slots=True)
class TimedEventRecurrence:
    weekdays: tuple[Weekday, ...] = ()


@dataclass(frozen=True, slots=True)
class TimedEventTime:
    time_base: TimeBase
    offset_seconds: int
    recurrence_base: str
    recurrence: TimedEventRecurrence


@dataclass(frozen=True, slots=True)
class TimedEvent:
    """Entry under ``/scripts/system-addon-timed-events/entries/<id>``.

    Tom's box has 17 entries (IDs 1-9, 13-14, 17, 19-23). Updates go via
    ``event/raise name=system-addon-timed-events.config parameter=actions=save;value=<JSON>``.
    """

    entry_id: str
    name: str
    scope: str
    conditions: UserActionConditions
    time: TimedEventTime
    actions: tuple[UserActionAction, ...]
    delete_counter: int
    last_executed: datetime | None


class ResponderTriggerType(str, Enum):
    """All trigger types observed in Tom's 102 scene-responder entries."""

    DEVICE_MSG = "device-msg"
    """Physical button press / device message. Fields: dsuid, msg, buttonIndex."""

    STATE_CHANGE = "state-change"
    """Zone- or cluster-state changed. Fields: name (state-name), state (value)."""

    ZONE_SCENE = "zone-scene"
    """Zone scene was called. Fields: zone, group, scene."""

    ADDON_STATE_CHANGE = "addon-state-change"
    """User-defined-state value changed. Fields: addon-id, name (state-id), state."""

    EVENT = "event"
    """Generic dSS event. Field: name (event-name)."""

    CUSTOM_EVENT = "custom-event"
    """High-level event (= user-action) fired. Field: event (action-id)."""

    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, raw: str | None) -> ResponderTriggerType:
        if not raw:
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class ResponderTrigger:
    """A single trigger condition inside a SceneResponder.

    Only the fields relevant for ``trigger_type`` are set; others are None.
    """

    trigger_type: ResponderTriggerType
    addon_id: str | None = None
    name: str | None = None
    state: str | None = None
    dsuid: str | None = None
    msg: int | None = None
    button_index: int | None = None
    zone: int | None = None
    group: int | None = None
    scene: int | None = None
    event: str | None = None

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> ResponderTrigger:
        return cls(
            trigger_type=ResponderTriggerType.from_raw(raw.get("type")),
            addon_id=raw.get("addon-id"),
            name=raw.get("name"),
            state=raw.get("state"),
            dsuid=raw.get("dsuid"),
            msg=raw.get("msg"),
            button_index=raw.get("buttonIndex"),
            zone=raw.get("zone"),
            group=raw.get("group"),
            scene=raw.get("scene"),
            event=raw.get("event"),
        )


@dataclass(frozen=True, slots=True)
class SceneResponder:
    """Entry under ``/scripts/system-addon-scene-responder/entries/<id>``.

    Tom's box has 102 entries, many of them system-managed (``md_*`` prefix).
    """

    entry_id: str
    name: str
    scope: str
    technical_role: str | None
    persistent_scope: bool
    delay: int
    conditions: UserActionConditions
    triggers: tuple[ResponderTrigger, ...]
    actions: tuple[UserActionAction, ...]
    singular_triggered: bool
    initial_triggered: bool
    last_executed: datetime | None

    @property
    def is_system_managed(self) -> bool:
        """True for entries auto-generated by other addons / system processes.

        Heuristic: numeric ids belong to the user (visible in the dSS web UI's
        scene-responder list and editable there). Non-numeric ids are
        auto-generated by other system-addons (motion-detector responders
        with the ``md_*`` prefix, fire-alarm reset, etc.) and should not be
        edited.

        Note: ``technical_role`` is ``"system"`` for all entries on Tom's box,
        even user-managed ones - it's not a reliable discriminator.
        """
        return not self.entry_id.isdigit()


class UserStateCategory(str, Enum):
    """Five sub-categories of user-defined states.

    - ``custom-states``: user-toggleable switches (set/reset labels)
    - ``combined-states``: logical AND of other states
    - ``triggered-states``: event-driven (set/reset triggers)
    - ``zone-sensor-states``: threshold over zone sensor value
    - ``device-sensor-states``: auto-generated by other addons (read-only)
    """

    DEVICE_SENSOR = "device-sensor-states"
    CUSTOM = "custom-states"
    COMBINED = "combined-states"
    TRIGGERED = "triggered-states"
    ZONE_SENSOR = "zone-sensor-states"


@dataclass(frozen=True, slots=True)
class UserStateValue:
    value: int
    label: str


@dataclass(frozen=True, slots=True)
class ZoneSensorStateConfig:
    """Threshold config for a ``zone-sensor-states`` user-state.

    The state becomes ``active`` when the configured sensor crosses
    ``active_value`` in the direction set by ``greater_on_set`` (True =
    when sensor rises above), and ``inactive`` when it crosses
    ``inactive_value`` the opposite way. Same values for both gives a
    sharp threshold; different values implement hysteresis.

    Sensor identification: zone + group + dSS sensor type code. See
    :class:`SensorType` for known type codes.
    """

    zone_id: int
    group_id: int
    type_id: int
    active_value: float
    inactive_value: float
    greater_on_set: bool = True


@dataclass(frozen=True, slots=True)
class CombinedCondition:
    """One AND-condition inside a ``combined-state``.

    ``addon_id`` is empty string for system states (e.g. ``zone.9.light``)
    and ``"system-addon-user-defined-states"`` for references to other
    user-defined states.

    Note: the dSS uses ``addonid`` (no hyphen) in the combined-state payload,
    inconsistent with ``addon-id`` elsewhere in the same protocol.
    """

    state: str
    value: str
    addon_id: str = ""

    @classmethod
    def for_user_state(cls, user_state_id: str, value: str = "active") -> CombinedCondition:
        """Build a condition referencing another user-defined-state."""
        return cls(
            state=user_state_id,
            value=value,
            addon_id="system-addon-user-defined-states",
        )

    @classmethod
    def for_system_state(cls, state_name: str, value: str) -> CombinedCondition:
        """Build a condition for a built-in system state (e.g. ``zone.5.light``)."""
        return cls(state=state_name, value=value, addon_id="")


@dataclass(frozen=True, slots=True)
class UserState:
    """User-defined state across all four sub-categories.

    Different categories use different subsets of the optional fields:

    - ``custom-states``: ``set_name``, ``reset_name``, ``show_on_phone``
    - ``combined-states``: ``combined_logic`` (AND/OR over other states)
    - ``triggered-states``: ``set_triggers``, ``reset_triggers``
    - ``device-sensor-states``: sensor-bound, mostly auto-generated
    """

    state_id: str
    name: str
    category: UserStateCategory
    current_value: int | str | None
    values: tuple[UserStateValue, ...] = ()

    # custom-states specific
    set_name: str | None = None
    reset_name: str | None = None
    show_on_phone: bool | None = None

    # combined-states specific - list of conditions ANDed together
    combined_and: tuple[CombinedCondition, ...] = ()

    # triggered-states specific - re-uses ResponderTrigger model
    set_triggers: tuple[ResponderTrigger, ...] = ()
    reset_triggers: tuple[ResponderTrigger, ...] = ()
    set_retriggering_lag: int = 0
    reset_retriggering_lag: int = 0

    # zone-sensor-states specific - threshold config
    zone_sensor: ZoneSensorStateConfig | None = None
