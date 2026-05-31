"""Shared entity bases for digitalSTROM.

Device-registry layout (two parallel sub-trees under the Apartment):

* **Apartment** - one device per ConfigEntry. Hub root.
* **Zone**     - logical sub-device per zone, ``via_device`` = Apartment.
                 Hosts zone-level scenes (Light On/Off, Shade Open/Close).
* **Circuit**  - hardware sub-device per dSM-Meter, ``via_device`` = Apartment.
                 Hosts no entities itself; physical Devices hang under it.
* **Device**   - physical-device sub-device per dSUID, ``via_device`` = Circuit
                 (falls back to Apartment if the device has no meter_dsuid).
                 Hosts diagnostic / sensor / binary_sensor / light / cover /
                 switch / event entities depending on capabilities.

A physical Device has two natural parents (Zone *and* Circuit); HA's
device registry only supports one, so we hang the hardware entities
under the Circuit to mirror the dSS hardware tree (the way the
ioBroker adapter shows it). Scenes live under the Zone instead.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pydigitalstrom import Circuit, Device, Zone

from .const import DOMAIN, MANUFACTURER
from .coordinator import DigitalStromCoordinator


def apartment_identifier(entry_id: str) -> tuple[str, str]:
    return (DOMAIN, entry_id)


def zone_identifier(entry_id: str, zone_id: int) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_zone_{zone_id}")


def device_identifier(entry_id: str, dsuid: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_device_{dsuid}")


def circuit_identifier(entry_id: str, circuit_dsuid: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_circuit_{circuit_dsuid}")


def _apartment_device_info(entry: ConfigEntry, apartment_name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={apartment_identifier(entry.entry_id)},
        manufacturer=MANUFACTURER,
        model="dSS Server",
        name=apartment_name or entry.title or "digitalSTROM",
    )


def _zone_device_info(
    entry: ConfigEntry, zone: Zone, apartment_name: str
) -> DeviceInfo:
    display = zone.name or f"Zone {zone.zone_id}"
    return DeviceInfo(
        identifiers={zone_identifier(entry.entry_id, zone.zone_id)},
        manufacturer=MANUFACTURER,
        model=f"Zone - {display}",
        name=f"dS {display}",
        via_device=apartment_identifier(entry.entry_id),
    )


def _circuit_device_info(entry: ConfigEntry, circuit: Circuit) -> DeviceInfo:
    display = circuit.name or f"dSM {circuit.dsid[-4:]}" if circuit.dsid else "dSM"
    parts = []
    if circuit.hw_version:
        parts.append(f"HW {circuit.hw_version}")
    if circuit.sw_version:
        parts.append(f"SW {circuit.sw_version}")
    model = f"dSM Meter ({' / '.join(parts)})" if parts else "dSM Meter"
    return DeviceInfo(
        identifiers={circuit_identifier(entry.entry_id, circuit.dsuid)},
        manufacturer=MANUFACTURER,
        model=model,
        name=f"dSM {display}",
        via_device=apartment_identifier(entry.entry_id),
        sw_version=circuit.sw_version or None,
        hw_version=circuit.hw_version or None,
    )


def _device_device_info(
    entry: ConfigEntry, device: Device, circuit: Circuit | None
) -> DeviceInfo:
    via = (
        circuit_identifier(entry.entry_id, circuit.dsuid)
        if circuit is not None
        else apartment_identifier(entry.entry_id)
    )
    return DeviceInfo(
        identifiers={device_identifier(entry.entry_id, device.dsuid)},
        manufacturer=MANUFACTURER,
        model=device.hw_info or "dSS Device",
        name=device.name or f"dS {device.display_id}",
        via_device=via,
    )


class ApartmentEntity(CoordinatorEntity[DigitalStromCoordinator]):
    """Base for apartment-level entities."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        apt = self.coordinator.data
        name = apt.name if apt is not None else ""
        return _apartment_device_info(self.coordinator.entry, name)


class ZoneEntity(CoordinatorEntity[DigitalStromCoordinator]):
    """Base for zone-level entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DigitalStromCoordinator, zone: Zone
    ) -> None:
        super().__init__(coordinator)
        self._zone_id = zone.zone_id
        self._zone_name = zone.name

    def _current_zone(self) -> Zone | None:
        apt = self.coordinator.data
        if apt is None:
            return None
        return apt.get_zone(self._zone_id)

    @property
    def device_info(self) -> DeviceInfo:
        apt = self.coordinator.data
        zone = self._current_zone()
        if zone is None or apt is None:
            # Best-effort fallback - keep entity registered even if the
            # zone disappeared from the inventory
            from pydigitalstrom import Zone as _Zone

            zone = _Zone(zone_id=self._zone_id, name=self._zone_name)  # type: ignore[call-arg]
        return _zone_device_info(
            self.coordinator.entry, zone, apt.name if apt else ""
        )


class CircuitEntity(CoordinatorEntity[DigitalStromCoordinator]):
    """Base for circuit (dSM-Meter) level entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DigitalStromCoordinator, circuit: Circuit
    ) -> None:
        super().__init__(coordinator)
        self._circuit_dsuid = circuit.dsuid
        self._cached_name = circuit.name
        self._cached_hw = circuit.hw_version
        self._cached_sw = circuit.sw_version

    def _current_circuit(self) -> Circuit | None:
        apt = self.coordinator.data
        if apt is None:
            return None
        for c in apt.circuits:
            if c.dsuid == self._circuit_dsuid:
                return c
        return None

    @property
    def device_info(self) -> DeviceInfo:
        circuit = self._current_circuit()
        if circuit is not None:
            return _circuit_device_info(self.coordinator.entry, circuit)
        # Fallback if the circuit is gone
        return DeviceInfo(
            identifiers={
                circuit_identifier(self.coordinator.entry_id, self._circuit_dsuid)
            },
            manufacturer=MANUFACTURER,
            name=self._cached_name or "dSM",
            via_device=apartment_identifier(self.coordinator.entry_id),
        )


class DeviceEntity(CoordinatorEntity[DigitalStromCoordinator]):
    """Base for physical-device-level entities (one per dSUID).

    Physical devices hang under their dSM-Circuit (hardware tree), not
    under their Zone (logical tree). The Zone side carries scenes only.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DigitalStromCoordinator, device: Device
    ) -> None:
        super().__init__(coordinator)
        self._dsuid = device.dsuid
        self._device_zone_id = device.zone_id
        self._meter_dsuid = device.meter_dsuid
        # Cache fields we need even if the device disappears from the snapshot
        self._cached_name = device.name
        self._cached_hw_info = device.hw_info

    def _current_device(self) -> Device | None:
        apt = self.coordinator.data
        if apt is None:
            return None
        return apt.get_device(self._dsuid)

    def _current_zone(self) -> Zone | None:
        apt = self.coordinator.data
        if apt is None:
            return None
        return apt.get_zone(self._device_zone_id)

    def _current_circuit(self) -> Circuit | None:
        apt = self.coordinator.data
        if apt is None or self._meter_dsuid is None:
            return None
        for c in apt.circuits:
            if c.dsuid == self._meter_dsuid:
                return c
        return None

    @property
    def device_info(self) -> DeviceInfo:
        device = self._current_device()
        circuit = self._current_circuit()
        if device is not None:
            return _device_device_info(self.coordinator.entry, device, circuit)
        # Fallback for orphaned entity
        return DeviceInfo(
            identifiers={device_identifier(self.coordinator.entry_id, self._dsuid)},
            manufacturer=MANUFACTURER,
            name=self._cached_name or self._dsuid,
            model=self._cached_hw_info or "dSS Device",
        )

    def _extra(self) -> dict[str, Any]:
        return {}
