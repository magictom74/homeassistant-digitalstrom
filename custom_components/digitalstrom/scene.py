"""Scene platform - one entity per zone+group preset.

For every zone the user has actually populated we expose:

* ``On``  / ``Off``  for the light group  (StandardScene.PRESET_1 / PRESET_0)
* ``Open`` / ``Close`` for the shade group (StandardScene.PRESET_1 / PRESET_0)

More advanced scenes (Wake, Bell, Standby, custom preset numbers) are
not entities yet - those are reachable via the ``digitalstrom.call_scene``
service.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pydigitalstrom import Group, StandardScene, Zone

from .const import DOMAIN
from .coordinator import DigitalStromCoordinator
from .entity import ZoneEntity

_LOGGER = logging.getLogger(__name__)


# Scene definitions per group type: (label, dSS-scene-number)
_LIGHT_PRESETS: tuple[tuple[str, int], ...] = (
    ("On", int(StandardScene.PRESET_1)),
    ("Off", int(StandardScene.PRESET_0)),
)
_SHADE_PRESETS: tuple[tuple[str, int], ...] = (
    ("Open", int(StandardScene.PRESET_1)),
    ("Close", int(StandardScene.PRESET_0)),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DigitalStromCoordinator = hass.data[DOMAIN][entry.entry_id]
    apt = coordinator.data
    if apt is None:
        return

    entities: list[Scene] = []
    for zone in apt.user_zones:
        light = zone.light_group
        shade = zone.shade_group
        if light is not None:
            for label, scene_id in _LIGHT_PRESETS:
                entities.append(
                    ZoneGroupSceneEntity(coordinator, zone, light, label, scene_id)
                )
        if shade is not None:
            for label, scene_id in _SHADE_PRESETS:
                entities.append(
                    ZoneGroupSceneEntity(coordinator, zone, shade, label, scene_id)
                )

    async_add_entities(entities)


class ZoneGroupSceneEntity(ZoneEntity, Scene):
    """One callable scene for a (zone, group, scene-number) triple."""

    def __init__(
        self,
        coordinator: DigitalStromCoordinator,
        zone: Zone,
        group: Group,
        label: str,
        scene_id: int,
    ) -> None:
        super().__init__(coordinator, zone)
        self._group_id = group.group_id
        self._group_name = group.name
        self._scene_id = scene_id
        group_label = _user_friendly_group_label(group)
        self._attr_unique_id = (
            f"{coordinator.entry_id}_scene_{zone.zone_id}_{group.group_id}_{scene_id}"
        )
        self._attr_name = f"{group_label} {label}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "zone_id": self._zone_id,
            "zone_name": self._zone_name,
            "group_id": self._group_id,
            "group_name": self._group_name,
            "scene_id": self._scene_id,
        }

    async def async_activate(self, **kwargs: Any) -> None:
        _LOGGER.debug(
            "[digitalstrom.scene] call_scene zone=%s group=%s scene=%s",
            self._zone_id,
            self._group_id,
            self._scene_id,
        )
        await self.coordinator.client.get(
            "/json/zone/callScene",
            params={
                "id": self._zone_id,
                "groupID": self._group_id,
                "sceneNumber": self._scene_id,
                "force": "false",
            },
        )


def _group_label(group: Group) -> str:
    """Fallback label when the dSS hasn't given the group a custom name."""
    if group.is_light:
        return "Light"
    if group.is_shade:
        return "Shade"
    return f"Group {group.group_id}"


# dSS often labels system groups by their color (yellow=light, grey=shade,
# blue=climate, cyan=audio, magenta=video, red=security, green=access,
# black=joker). User-set names should win, but those raw color names are
# the dSS default and not helpful in HA - swap them out.
_DSS_COLOR_NAMES: frozenset[str] = frozenset({
    "yellow", "grey", "gray", "blue", "cyan",
    "magenta", "red", "green", "black",
})


def _user_friendly_group_label(group: Group) -> str:
    """Pick the best name for a group.

    Priority:
      1. Real custom name from the user (e.g. "Living-Lamps") - kept.
      2. dSS default color name (yellow/grey/...) - mapped to a semantic
         label based on group_id (Light/Shade/...).
      3. Empty name - same semantic label.
    """
    name = (group.name or "").strip()
    if name and name.lower() not in _DSS_COLOR_NAMES:
        return name
    return _group_label(group)
