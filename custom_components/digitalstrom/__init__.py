"""The digitalSTROM integration."""

from __future__ import annotations

import contextlib
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.httpx_client import get_async_client

from pydigitalstrom import (
    AppToken,
    ButtonClickEvent,
    CallSceneEvent,
    DssClient,
    DssEvent,
    StateChangeEvent,
)

from . import config_flow  # noqa: F401 - pre-import for HA loader
from .const import (
    CONF_APP_TOKEN,
    CONF_HOST,
    CONF_PORT,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    EVENT_BUTTON_CLICK,
    EVENT_CALL_SCENE,
    EVENT_STATE_CHANGE,
    SERVICE_CALL_SCENE,
    SERVICE_UNDO_SCENE,
)
from .coordinator import DigitalStromCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SCENE,
    Platform.SENSOR,
]


CALL_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required("zone_id"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("group_id", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Required("scene_id"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("force", default=False): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    token = entry.data[CONF_APP_TOKEN]
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    client = DssClient(
        host,
        AppToken(value=token, application_name="HomeAssistant"),
        port=port,
        verify_ssl=verify_ssl,
        http_client=get_async_client(hass, verify_ssl=verify_ssl),
    )

    try:
        await client.login()
    except Exception as exc:
        with contextlib.suppress(Exception):
            await client.close()
        raise ConfigEntryNotReady(f"dSS login failed: {exc}") from exc

    coordinator = DigitalStromCoordinator(hass, entry=entry, client=client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        with contextlib.suppress(Exception):
            await client.close()
        raise ConfigEntryNotReady(f"Apartment fetch failed: {exc}") from exc

    # Bridge dSS events onto the HA bus so user automations can react.
    coordinator.add_event_handler(lambda ev: _fan_out_to_bus(hass, entry, ev))

    await coordinator.async_start_event_stream()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: DigitalStromCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop_event_stream()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        with contextlib.suppress(Exception):
            await coordinator.client.close()
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_CALL_SCENE)
            hass.services.async_remove(DOMAIN, SERVICE_UNDO_SCENE)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# Event -> HA bus
# ---------------------------------------------------------------------------


def _fan_out_to_bus(hass: HomeAssistant, entry: ConfigEntry, ev: DssEvent) -> None:
    """Translate certain dSS events into HA bus events for automations."""
    base = {"entry_id": entry.entry_id, "event_class": type(ev).__name__}
    if isinstance(ev, CallSceneEvent):
        hass.bus.async_fire(EVENT_CALL_SCENE, {
            **base,
            "zone_id": ev.zone_id,
            "group_id": ev.group_id,
            "scene_id": ev.scene_id,
            "call_origin": ev.call_origin,
        })
    elif isinstance(ev, ButtonClickEvent):
        hass.bus.async_fire(EVENT_BUTTON_CLICK, {
            **base,
            "dsid": ev.dsid,
            "button_index": ev.button_index,
            "click_type": ev.click_type,
            "click_type_enum": ev.click_type_enum.name if ev.click_type_enum else None,
        })
    elif isinstance(ev, StateChangeEvent):
        hass.bus.async_fire(EVENT_STATE_CHANGE, {
            **base,
            "state_name": ev.state_name,
            "state": ev.state,
            "value": ev.value,
            "old_value": ev.old_value,
            "call_origin": ev.call_origin,
        })
    # Other event types stay on the coordinator's internal stream only.


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_CALL_SCENE):
        return

    def _first_coordinator() -> DigitalStromCoordinator | None:
        entries = hass.data.get(DOMAIN, {})
        return next(iter(entries.values()), None) if entries else None

    async def _call_scene(call: ServiceCall) -> None:
        coord = _first_coordinator()
        if coord is None:
            raise ValueError("No digitalSTROM dSS configured")
        zone = int(call.data["zone_id"])
        group = int(call.data.get("group_id", 0))
        scene = int(call.data["scene_id"])
        force = bool(call.data.get("force", False))
        await coord.client.get(
            "/json/zone/callScene",
            params={
                "id": zone,
                "groupID": group,
                "sceneNumber": scene,
                "force": "true" if force else "false",
            },
        )

    async def _undo_scene(call: ServiceCall) -> None:
        coord = _first_coordinator()
        if coord is None:
            raise ValueError("No digitalSTROM dSS configured")
        zone = int(call.data["zone_id"])
        group = int(call.data.get("group_id", 0))
        scene = int(call.data["scene_id"])
        await coord.client.get(
            "/json/zone/undoScene",
            params={"id": zone, "groupID": group, "sceneNumber": scene},
        )

    hass.services.async_register(DOMAIN, SERVICE_CALL_SCENE, _call_scene, schema=CALL_SCENE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_UNDO_SCENE, _undo_scene, schema=CALL_SCENE_SCHEMA)
