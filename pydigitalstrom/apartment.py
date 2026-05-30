"""Apartment loader - fetches and parses the dSS apartment structure.

The main entry is :func:`fetch_apartment`, which aggregates four dSS
endpoints into one immutable :class:`Apartment` tree:

- ``/json/apartment/getName`` - apartment display name
- ``/json/system/getDSID`` - apartment-level dSID / dSUID
- ``/json/apartment/getStructure`` - zones + devices + groups + clusters
- ``/json/apartment/getCircuits`` - bus master meters

Parsing logic is split out as pure functions (:func:`parse_apartment_structure`,
:func:`parse_circuits`) so tests can run against fixture JSON without a network.
"""

from __future__ import annotations

import logging
from typing import Any

from .client import DssClient
from .models import (
    Apartment,
    BinaryInput,
    Circuit,
    Cluster,
    Device,
    DeviceCapabilities,
    Group,
    OutputChannel,
    OutputMode,
    Sensor,
    SensorType,
    Zone,
)

_LOGGER = logging.getLogger(__name__)


async def fetch_apartment(client: DssClient) -> Apartment:
    """Load the full apartment tree from the dSS.

    Args:
        client: Authenticated DssClient. Login happens lazily.

    Returns:
        Immutable Apartment with all zones, devices, groups, clusters, and
        circuits populated.
    """
    name = await fetch_apartment_name(client)
    dsid_info = await client.get("/json/system/getDSID")
    structure_raw = await client.get("/json/apartment/getStructure")
    circuits_raw = await client.get("/json/apartment/getCircuits")

    dsid = str(dsid_info.get("dSID", ""))
    dsuid = str(dsid_info.get("dSUID", ""))

    apt = parse_apartment_structure(
        structure_raw,
        name=name,
        dsid=dsid,
        dsuid=dsuid,
    )
    circuits = parse_circuits(circuits_raw)

    # Replace circuits in the parsed apartment (immutable rebuild)
    return Apartment(
        name=apt.name,
        dsid=apt.dsid,
        dsuid=apt.dsuid,
        zones=apt.zones,
        clusters=apt.clusters,
        circuits=circuits,
    )


async def fetch_apartment_name(client: DssClient) -> str:
    """Just the apartment display name."""
    result = await client.get("/json/apartment/getName")
    return str(result.get("name", ""))


async def fetch_apartment_circuits(client: DssClient) -> tuple[Circuit, ...]:
    """Just the dSM-meter list."""
    raw = await client.get("/json/apartment/getCircuits")
    return parse_circuits(raw)


# ---------------------------------------------------------------------------
# Pure parsers (no I/O - testable with fixture JSON)
# ---------------------------------------------------------------------------


def parse_apartment_structure(
    raw: dict[str, Any],
    *,
    name: str = "",
    dsid: str = "",
    dsuid: str = "",
) -> Apartment:
    """Parse the ``/json/apartment/getStructure`` result.

    Circuits are NOT parsed here (they come from getCircuits) - the returned
    Apartment has ``circuits=()``. Use :func:`fetch_apartment` for the merged
    full tree.
    """
    apartment = raw.get("apartment") or {}

    zones = tuple(_parse_zone(z) for z in apartment.get("zones", []))
    clusters = tuple(_parse_cluster(c) for c in apartment.get("clusters", []))

    return Apartment(
        name=name,
        dsid=dsid,
        dsuid=dsuid,
        zones=zones,
        clusters=clusters,
        circuits=(),
    )


def parse_circuits(raw: dict[str, Any]) -> tuple[Circuit, ...]:
    """Parse ``/json/apartment/getCircuits`` result."""
    items = raw.get("circuits") or []
    return tuple(_parse_circuit(c) for c in items)


# ---------------------------------------------------------------------------
# Internal element parsers
# ---------------------------------------------------------------------------


def _parse_zone(raw: dict[str, Any]) -> Zone:
    zone_id = int(raw.get("id", 0))
    devices = tuple(_parse_device(d, zone_id) for d in raw.get("devices", []))
    groups = tuple(_parse_group(g, devices) for g in raw.get("groups", []))
    return Zone(
        zone_id=zone_id,
        name=str(raw.get("name", "")),
        devices=devices,
        groups=groups,
    )


def _parse_group(raw: dict[str, Any], zone_devices: tuple[Device, ...]) -> Group:
    group_id = int(raw.get("id", 0))

    # ``devices`` field on a group is sometimes an array of DSUIDs, sometimes
    # absent (zone-level group inheritance). Fall back to "all zone devices
    # that list this group" - that's how the dSS UI computes membership.
    explicit = raw.get("devices")
    if isinstance(explicit, list) and explicit:
        dsuids = tuple(str(x) for x in explicit if x)
    else:
        dsuids = tuple(d.dsuid for d in zone_devices if group_id in d.groups)

    return Group(
        group_id=group_id,
        name=str(raw.get("name", "")),
        color=int(raw.get("color", 0)),
        application_type=int(raw.get("applicationType", 0)),
        is_present=bool(raw.get("isPresent", True)),
        device_dsuids=dsuids,
    )


def _parse_cluster(raw: dict[str, Any]) -> Cluster:
    return Cluster(
        cluster_id=int(raw.get("id", 0)),
        name=str(raw.get("name", "")),
        color=int(raw.get("color", 0)),
        application_type=int(raw.get("applicationType", 0)),
        is_present=bool(raw.get("isPresent", True)),
    )


def _parse_device(raw: dict[str, Any], zone_id: int) -> Device:
    groups_raw = raw.get("groups", [])
    groups = tuple(int(g) for g in groups_raw if isinstance(g, int)) if groups_raw else ()

    sensors_raw = raw.get("sensors", []) or []
    binary_inputs_raw = raw.get("binaryInputs", []) or []
    output_channels_raw = raw.get("outputChannels", []) or []

    sensors = tuple(_parse_sensor(s) for s in sensors_raw)
    binary_inputs = tuple(_parse_binary_input(b) for b in binary_inputs_raw)
    output_channels = tuple(_parse_output_channel(o) for o in output_channels_raw)

    capabilities = DeviceCapabilities(
        has_output=bool(output_channels) or _to_int(raw.get("outputMode")) not in (0, None),
        has_input=raw.get("buttonInputMode") is not None,
        has_sensors=bool(sensors),
        has_binary_inputs=bool(binary_inputs),
    )

    return Device(
        dsuid=str(raw.get("dSUID", "")),
        dsid=str(raw.get("id", raw.get("dSID", ""))),
        display_id=str(raw.get("DisplayID", "")),
        name=str(raw.get("name", "")),
        zone_id=zone_id,
        hw_info=str(raw.get("HWInfo", "")),
        function_id=_to_int(raw.get("functionID")) or 0,
        product_id=_to_int(raw.get("productID")) or 0,
        revision_id=_to_int(raw.get("revisionID")) or 0,
        model=raw.get("model"),
        gtin=str(raw["GTIN"]) if raw.get("GTIN") is not None else None,
        is_present=bool(raw.get("isPresent", True)),
        is_valid=bool(raw.get("isValid", True)),
        last_discovered=raw.get("lastDiscovered"),
        first_seen=raw.get("firstSeen"),
        output_mode=OutputMode.from_raw(_to_int(raw.get("outputMode"))),
        output_channels=output_channels,
        button_input_mode=_to_int(raw.get("buttonInputMode")),
        button_input_index=_to_int(raw.get("buttonInputIndex")),
        button_id=_to_int(raw.get("buttonID")),
        groups=groups,
        capabilities=capabilities,
        sensors=sensors,
        binary_inputs=binary_inputs,
        meter_dsuid=raw.get("meterDSUID"),
    )


def _parse_sensor(raw: dict[str, Any]) -> Sensor:
    return Sensor(
        sensor_index=int(raw.get("sensorIndex", 0)),
        sensor_type=SensorType.from_raw(raw.get("sensorType")),
        last_value=_to_float(raw.get("sensorValueFloat") or raw.get("sensorValue")),
        last_value_ts=None,  # Structure JSON does not include timestamps; available via getSensorValue
    )


def _parse_binary_input(raw: dict[str, Any]) -> BinaryInput:
    return BinaryInput(
        input_index=int(raw.get("inputIndex", 0)),
        input_type=int(raw.get("inputType", 0)),
        target_type=int(raw.get("targetType", 0)),
        target_group=int(raw.get("targetGroup", 0)),
        state=_to_bool(raw.get("state")),
    )


def _parse_output_channel(raw: dict[str, Any]) -> OutputChannel:
    channel_id = str(raw.get("channelId", ""))
    return OutputChannel(
        channel_index=int(raw.get("channelIndex", 0)),
        channel_id=channel_id,
        channel_type=str(raw.get("channelType", channel_id)),
        channel_name=raw.get("channelName"),
    )


def _parse_circuit(raw: dict[str, Any]) -> Circuit:
    return Circuit(
        name=str(raw.get("name", "")),
        dsid=str(raw.get("dsid", "")),
        dsuid=str(raw.get("dSUID", "")),
        hw_version=raw.get("hwVersion"),
        sw_version=raw.get("swVersion"),
        arm_sw_version=raw.get("armSwVersion"),
        api_version=raw.get("apiVersion"),
        is_present=bool(raw.get("isPresent", True)),
        is_valid=bool(raw.get("isValid", True)),
        bus_member_type=_to_int(raw.get("busMemberType")),
    )


# ---------------------------------------------------------------------------
# Tiny helpers - dSS occasionally returns strings where ints are expected
# ---------------------------------------------------------------------------


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on", "active")
    return None
