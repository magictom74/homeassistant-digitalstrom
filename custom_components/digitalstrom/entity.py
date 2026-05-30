"""Shared entity bases for digitalSTROM.

Device-registry layout:

* **Apartment** - one device per ConfigEntry. Hosts apartment-level
  entities (Power, Energy, last-event diagnostics).
* **Zone** - one sub-device per zone, ``via_device`` linked to the
  apartment. Hosts zone-level scenes + sensors.
* **Device** - one sub-device per physical dSS-Device (light, shade,
  binary input). Hosts the light/cover entity itself.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pydigitalstrom import Device, Zone

from .const import DOMAIN, MANUFACTURER
from .coordinator import DigitalStromCoordinator


def apartment_identifier(entry_id: str) -> tuple[str, str]:
    return (DOMAIN, entry_id)


def zone_identifier(entry_id: str, zone_id: int) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_zone_{zone_id}")


def device_identifier(entry_id: str, dsuid: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_device_{dsuid}")


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


def _device_device_info(
    entry: ConfigEntry, device: Device, zone: Zone | None
) -> DeviceInfo:
    via = (
        zone_identifier(entry.entry_id, zone.zone_id)
        if zone is not None
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


class DeviceEntity(CoordinatorEntity[DigitalStromCoordinator]):
    """Base for physical-device-level entities (one per dSUID)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DigitalStromCoordinator, device: Device
    ) -> None:
        super().__init__(coordinator)
        self._dsuid = device.dsuid
        self._device_zone_id = device.zone_id
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

    @property
    def device_info(self) -> DeviceInfo:
        device = self._current_device()
        zone = self._current_zone()
        if device is not None:
            return _device_device_info(self.coordinator.entry, device, zone)
        # Fallback for orphaned entity
        return DeviceInfo(
            identifiers={device_identifier(self.coordinator.entry_id, self._dsuid)},
            manufacturer=MANUFACTURER,
            name=self._cached_name or self._dsuid,
            model=self._cached_hw_info or "dSS Device",
        )

    def _extra(self) -> dict[str, Any]:
        return {}
