"""State container for one digitalSTROM dSS.

The dSS exposes a REST API for state reads + scene calls and a
long-poll event stream. We do an initial inventory fetch
(``fetch_apartment``) at setup, then run an EventStream in the
background to keep state and to surface events on the HA bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from pydigitalstrom import (
    Apartment,
    DssClient,
    DssEvent,
    EventStream,
    fetch_apartment,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DigitalStromCoordinator(DataUpdateCoordinator[Apartment]):
    """Holds the apartment inventory + drives the event stream."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry: ConfigEntry,
        client: DssClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{entry.entry_id}",
            update_interval=None,  # push-driven via the event stream
        )
        self.entry = entry
        self.entry_id = entry.entry_id
        self.client = client
        self._event_task: asyncio.Task[None] | None = None
        self._stream: EventStream | None = None
        self._stopped = asyncio.Event()
        self._last_event: DssEvent | None = None
        self._event_handlers: list = []

    # ------------------------------------------------------------------
    # initial fetch
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> Apartment:
        _LOGGER.debug("[digitalstrom.coordinator] fetching apartment inventory")
        apt = await fetch_apartment(self.client)
        _LOGGER.info(
            "[digitalstrom.coordinator] loaded apartment %r (%d zones, %d devices)",
            apt.name,
            len(apt.zones),
            len(apt.all_devices),
        )
        return apt

    # ------------------------------------------------------------------
    # event stream lifecycle
    # ------------------------------------------------------------------

    async def async_start_event_stream(self) -> None:
        if self._event_task is not None and not self._event_task.done():
            return
        self._stopped.clear()
        self._stream = EventStream(self.client)
        self._event_task = asyncio.create_task(self._run_stream(), name="digitalstrom-events")

    async def async_stop_event_stream(self) -> None:
        self._stopped.set()
        if self._stream is not None:
            with contextlib.suppress(Exception):
                await self._stream.close()
            self._stream = None
        if self._event_task is not None:
            self._event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_task
            self._event_task = None

    async def _run_stream(self) -> None:
        assert self._stream is not None
        try:
            async for ev in self._stream:
                if self._stopped.is_set():
                    break
                self._last_event = ev
                # Fan out to whoever registered (platforms, services)
                for handler in list(self._event_handlers):
                    try:
                        handler(ev)
                    except Exception:
                        _LOGGER.exception(
                            "[digitalstrom.coordinator] event handler %r raised", handler
                        )
                self.async_update_listeners()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("[digitalstrom.coordinator] event stream crashed")

    def add_event_handler(self, handler) -> None:
        self._event_handlers.append(handler)

    def remove_event_handler(self, handler) -> None:
        if handler in self._event_handlers:
            self._event_handlers.remove(handler)

    @property
    def last_event(self) -> DssEvent | None:
        return self._last_event

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        apt = self.data
        return {
            "zones": len(apt.zones) if apt is not None else 0,
            "devices": len(apt.all_devices) if apt is not None else 0,
            "circuits": len(apt.circuits) if apt is not None else 0,
            "clusters": len(apt.clusters) if apt is not None else 0,
            "event_stream_running": self._event_task is not None
            and not self._event_task.done(),
            "last_event_type": (
                type(self._last_event).__name__ if self._last_event else None
            ),
        }
