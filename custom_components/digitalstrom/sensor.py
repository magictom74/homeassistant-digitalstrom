"""Sensor platform - diagnostic info per physical device.

v0.3.0: just a single "Hardware" diagnostic sensor per device that
carries dSUID, hw_info, klemmen-type, output_mode, capabilities, and
zone-assignment. Real sensor values (temperature, humidity, power,
energy, ...) from ``Device.sensors`` come in v0.3.1.

The diagnostic sensor doubles as the entry point for the device card
in the HA UI - just by registering an entity the device shows up.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pydigitalstrom import Device

from .const import DOMAIN
from .coordinator import DigitalStromCoordinator
from .entity import DeviceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DigitalStromCoordinator = hass.data[DOMAIN][entry.entry_id]
    apt = coordinator.data
    if apt is None:
        return

    entities: list[SensorEntity] = []
    for device in apt.all_devices:
        entities.append(DeviceHardwareSensor(coordinator, device))

    async_add_entities(entities)


class DeviceHardwareSensor(DeviceEntity, SensorEntity):
    """One-line diagnostic per physical device.

    State: 'present' / 'missing' (from is_present + is_valid).
    Attributes: all the static identification fields you'd want to know
    when troubleshooting a klemme.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Hardware"

    def __init__(self, coordinator: DigitalStromCoordinator, device: Device) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = (
            f"{coordinator.entry_id}_device_{device.dsuid}_hardware"
        )

    @property
    def native_value(self) -> str:
        d = self._current_device()
        if d is None:
            return "missing"
        if d.is_present and d.is_valid:
            return "present"
        if d.is_present:
            return "invalid"
        return "missing"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._current_device()
        if d is None:
            return {}
        zone = self._current_zone()
        return {
            "dsuid": d.dsuid,
            "dsid": d.dsid,
            "display_id": d.display_id,
            "hw_info": d.hw_info,
            "model": d.model or "",
            "function_id": d.function_id,
            "product_id": d.product_id,
            "revision_id": d.revision_id,
            "output_mode": d.output_mode.name,
            "has_output": d.capabilities.has_output,
            "has_input": d.capabilities.has_input,
            "has_sensors": d.capabilities.has_sensors,
            "has_binary_inputs": d.capabilities.has_binary_inputs,
            "sensor_count": len(d.sensors),
            "binary_input_count": len(d.binary_inputs),
            "button_input_index": d.button_input_index,
            "zone_id": d.zone_id,
            "zone_name": zone.name if zone is not None else "",
            "groups": list(d.groups),
        }
