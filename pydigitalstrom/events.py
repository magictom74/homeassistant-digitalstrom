"""Long-poll event subscription, dispatcher, and typed event classes.

The dSS notifies subscribers via long-poll: client calls ``event/subscribe``
once per event type, then loops on ``event/get`` which blocks up to the
configured timeout and returns accumulated events. :class:`EventStream`
abstracts this into an async iterator; :class:`EventDispatcher` adds
handler-registration on top.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import TracebackType
from typing import Any

from .client import DssClient
from .exceptions import DssError, DssSubscriptionError
from .models import ClickType, EventSource, SensorType

_LOGGER = logging.getLogger(__name__)


EVENT_NAMES: tuple[str, ...] = (
    "callScene",
    "undoScene",
    "buttonClick",
    "stateChange",
    "deviceBinaryInputEvent",
    "deviceSensorEvent",
    "zoneSensorValue",
    "zoneSensorError",
    "highlevelevent",
    "DeviceEvent",
    "executionDenied",
    "running",
    "model_ready",
)
"""Default subscription set covering every event observed on Tom's box."""


# ---------------------------------------------------------------------------
# Event base + typed subclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DssEvent:
    """Base class for all dSS events.

    Subclasses add typed property accessors but do not store extra state -
    typed values are derived from :attr:`properties` on demand.
    """

    name: str
    properties: Mapping[str, str]
    source: EventSource
    received_at: datetime

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any], *, received_at: datetime | None = None) -> DssEvent:
        """Factory: dispatch to the right subclass based on the event name."""
        name = str(raw.get("name", ""))
        source_raw = raw.get("source") or {}
        props_raw = raw.get("properties") or {}
        props = {str(k): str(v) for k, v in props_raw.items()}
        source = EventSource.from_raw(source_raw) if isinstance(source_raw, Mapping) else EventSource(
            set_expr="", zone_id=0, group_id=0, dsid=None,
            is_apartment=False, is_group=False, is_device=False,
        )
        ts = received_at or datetime.now(timezone.utc)

        target_cls = _EVENT_CLASS_MAP.get(name, cls)
        return target_cls(name=name, properties=props, source=source, received_at=ts)


def _int_prop(props: Mapping[str, str], key: str, default: int = 0) -> int:
    raw = props.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_prop(props: Mapping[str, str], key: str, default: float = 0.0) -> float:
    raw = props.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class CallSceneEvent(DssEvent):
    @property
    def zone_id(self) -> int:
        return _int_prop(self.properties, "zoneID")

    @property
    def group_id(self) -> int:
        return _int_prop(self.properties, "groupID")

    @property
    def scene_id(self) -> int:
        return _int_prop(self.properties, "sceneID")

    @property
    def call_origin(self) -> int:
        return _int_prop(self.properties, "callOrigin")

    @property
    def origin_dsuid(self) -> str:
        return self.properties.get("originDSUID", "")

    @property
    def origin_token(self) -> str:
        return self.properties.get("originToken", "")


@dataclass(frozen=True, slots=True)
class UndoSceneEvent(DssEvent):
    @property
    def zone_id(self) -> int:
        return _int_prop(self.properties, "zoneID")

    @property
    def group_id(self) -> int:
        return _int_prop(self.properties, "groupID")

    @property
    def call_origin(self) -> int:
        return _int_prop(self.properties, "callOrigin")


@dataclass(frozen=True, slots=True)
class ButtonClickEvent(DssEvent):
    @property
    def button_index(self) -> int:
        return _int_prop(self.properties, "buttonIndex")

    @property
    def click_type(self) -> int:
        return _int_prop(self.properties, "clickType")

    @property
    def click_type_enum(self) -> ClickType:
        return ClickType.from_raw(self.properties.get("clickType"))

    @property
    def dsid(self) -> str | None:
        return self.source.dsid


@dataclass(frozen=True, slots=True)
class StateChangeEvent(DssEvent):
    @property
    def state_name(self) -> str:
        return self.properties.get("statename", "")

    @property
    def state(self) -> str:
        return self.properties.get("state", "")

    @property
    def value(self) -> str:
        return self.properties.get("value", "")

    @property
    def old_value(self) -> str:
        return self.properties.get("oldvalue", "")

    @property
    def call_origin(self) -> int | None:
        raw = self.properties.get("callOrigin")
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class DeviceBinaryInputEvent(DssEvent):
    @property
    def input_state(self) -> int:
        return _int_prop(self.properties, "inputState")

    @property
    def input_index(self) -> int:
        return _int_prop(self.properties, "inputIndex")

    @property
    def input_type(self) -> int:
        return _int_prop(self.properties, "inputType")

    @property
    def dsid(self) -> str | None:
        return self.source.dsid


@dataclass(frozen=True, slots=True)
class DeviceSensorEvent(DssEvent):
    @property
    def sensor_index(self) -> int:
        return _int_prop(self.properties, "sensorIndex")

    @property
    def sensor_type_raw(self) -> int:
        return _int_prop(self.properties, "sensorType")

    @property
    def sensor_type(self) -> SensorType:
        return SensorType.from_raw(self.properties.get("sensorType"))

    @property
    def sensor_value(self) -> int:
        return _int_prop(self.properties, "sensorValue")

    @property
    def sensor_value_float(self) -> float:
        return _float_prop(self.properties, "sensorValueFloat")

    @property
    def dsid(self) -> str | None:
        return self.source.dsid


@dataclass(frozen=True, slots=True)
class ZoneSensorValueEvent(DssEvent):
    @property
    def zone_id(self) -> int:
        return self.source.zone_id

    @property
    def sensor_type_raw(self) -> int:
        return _int_prop(self.properties, "sensorType")

    @property
    def sensor_type(self) -> SensorType:
        return SensorType.from_raw(self.properties.get("sensorType"))

    @property
    def sensor_value(self) -> int:
        return _int_prop(self.properties, "sensorValue")

    @property
    def sensor_value_float(self) -> float:
        return _float_prop(self.properties, "sensorValueFloat")

    @property
    def origin_dsid(self) -> str:
        return self.properties.get("originDSID", "")


@dataclass(frozen=True, slots=True)
class ZoneSensorErrorEvent(DssEvent):
    @property
    def zone_id(self) -> int:
        return self.source.zone_id

    @property
    def sensor_type_raw(self) -> int:
        return _int_prop(self.properties, "sensorType")

    @property
    def sensor_type(self) -> SensorType:
        return SensorType.from_raw(self.properties.get("sensorType"))

    @property
    def last_value_ts_iso(self) -> str:
        return self.properties.get("lastValueTS", "")

    @property
    def last_value_ts(self) -> datetime | None:
        """Parsed timestamp, or None if missing.

        A value of ``1970-01-01T00:00:00.000Z`` means the sensor never reported -
        the property is set but parsing succeeds. Callers should explicitly
        check for the epoch zero if they want to treat it as "never seen".
        """
        raw = self.properties.get("lastValueTS")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class HighLevelEvent(DssEvent):
    """User-defined action fired.

    ``action_id`` references ``/usr/events/<id>``.
    """

    @property
    def action_id(self) -> str:
        return self.properties.get("id", "")

    @property
    def source_name(self) -> str:
        return self.properties.get("source-name", "")


@dataclass(frozen=True, slots=True)
class DeviceEvent(DssEvent):
    """Generic device event - properties vary by trigger."""


@dataclass(frozen=True, slots=True)
class ExecutionDeniedEvent(DssEvent):
    """A call was blocked by conditions."""

    @property
    def reason(self) -> str:
        return self.properties.get("reason", "")


@dataclass(frozen=True, slots=True)
class RunningEvent(DssEvent):
    """dSS started or restarted - library re-subscribes on receipt."""


@dataclass(frozen=True, slots=True)
class ModelReadyEvent(DssEvent):
    """Apartment model refreshed - cached :class:`Apartment` should be invalidated."""


_EVENT_CLASS_MAP: dict[str, type[DssEvent]] = {
    "callScene": CallSceneEvent,
    "undoScene": UndoSceneEvent,
    "buttonClick": ButtonClickEvent,
    "stateChange": StateChangeEvent,
    "deviceBinaryInputEvent": DeviceBinaryInputEvent,
    "deviceSensorEvent": DeviceSensorEvent,
    "zoneSensorValue": ZoneSensorValueEvent,
    "zoneSensorError": ZoneSensorErrorEvent,
    "highlevelevent": HighLevelEvent,
    "DeviceEvent": DeviceEvent,
    "executionDenied": ExecutionDeniedEvent,
    "running": RunningEvent,
    "model_ready": ModelReadyEvent,
}


def parse_event(raw: Mapping[str, Any]) -> DssEvent:
    """Public factory function used by EventStream + tests."""
    return DssEvent.from_raw(raw)


# ---------------------------------------------------------------------------
# EventStream - long-poll loop as async iterator
# ---------------------------------------------------------------------------


class EventStream:
    """Long-poll event subscription with auto-reconnect.

    Usage::

        async with EventStream(client) as stream:
            async for event in stream:
                if isinstance(event, CallSceneEvent):
                    ...

    The stream owns its subscription lifecycle: subscribe-all on enter,
    unsubscribe-all on exit. On long-poll failures it retries with
    exponential backoff. A :class:`RunningEvent` triggers an automatic
    re-subscribe (the dSS may have rebooted).
    """

    def __init__(
        self,
        client: DssClient,
        *,
        subscription_id: int | None = None,
        events: Iterable[str] = EVENT_NAMES,
        poll_timeout_ms: int = 30000,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        self._client = client
        self._subscription_id = subscription_id if subscription_id is not None else random.randint(
            10_000_000, 99_999_999
        )
        self._events = tuple(events)
        self._poll_timeout_ms = poll_timeout_ms
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._running = False
        self._event_count = 0
        self._reconnect_count = 0
        self._stop_event = asyncio.Event()
        self._buffer: asyncio.Queue[DssEvent] = asyncio.Queue()

    @property
    def subscription_id(self) -> int:
        return self._subscription_id

    @property
    def events(self) -> tuple[str, ...]:
        return self._events

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    async def __aenter__(self) -> EventStream:
        await self.subscribe_all()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="pydss-events-poll")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    def __aiter__(self) -> AsyncIterator[DssEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[DssEvent]:
        while self._running or not self._buffer.empty():
            try:
                event = await asyncio.wait_for(self._buffer.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if not self._running:
                    break
                continue
            yield event

    async def subscribe_all(self) -> None:
        """Subscribe to every event in ``self._events`` under the shared id."""
        for name in self._events:
            try:
                await self._client.get(
                    "/json/event/subscribe",
                    params={"name": name, "subscriptionID": self._subscription_id},
                )
            except DssError as exc:
                raise DssSubscriptionError(
                    f"[pydss.events] subscribe {name!r} failed: {exc}"
                ) from exc
        _LOGGER.info(
            "[pydss.events] Subscribed %d events under id %d",
            len(self._events),
            self._subscription_id,
        )

    async def unsubscribe_all(self) -> None:
        """Best-effort cleanup. Errors are logged but not raised."""
        for name in self._events:
            try:
                await self._client.get(
                    "/json/event/unsubscribe",
                    params={"name": name, "subscriptionID": self._subscription_id},
                )
            except Exception as exc:
                _LOGGER.debug("[pydss.events] unsubscribe %s failed: %s", name, exc)

    async def stop(self) -> None:
        """Stop the polling loop and unsubscribe."""
        self._running = False
        self._stop_event.set()
        if hasattr(self, "_poll_task"):
            try:
                await asyncio.wait_for(self._poll_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._poll_task.cancel()
        await self.unsubscribe_all()

    async def _poll_loop(self) -> None:
        backoff = self._backoff_initial
        while self._running and not self._stop_event.is_set():
            try:
                raw_events = await self._client.event_long_poll(
                    self._subscription_id,
                    timeout_ms=self._poll_timeout_ms,
                )
            except DssError as exc:
                _LOGGER.warning("[pydss.events] poll failed: %s - retry in %.1fs", exc, backoff)
                self._reconnect_count += 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break  # stop signalled
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, self._backoff_max)
                continue
            except asyncio.CancelledError:
                break

            backoff = self._backoff_initial

            for raw in raw_events:
                event = DssEvent.from_raw(raw)
                self._event_count += 1
                await self._buffer.put(event)

                if isinstance(event, RunningEvent):
                    _LOGGER.info("[pydss.events] dSS restart detected - re-subscribing")
                    try:
                        await self.subscribe_all()
                    except DssError as exc:
                        _LOGGER.error("[pydss.events] re-subscribe failed: %s", exc)


# ---------------------------------------------------------------------------
# EventDispatcher - higher-level handler registration
# ---------------------------------------------------------------------------


Handler = Callable[[DssEvent], Awaitable[None]]


class EventDispatcher:
    """Register typed handlers and dispatch events from an :class:`EventStream`.

    Usage::

        dispatcher = EventDispatcher(stream)

        @dispatcher.on(CallSceneEvent)
        async def on_scene(event: CallSceneEvent) -> None:
            print(event.zone_id, event.scene_id)

        await dispatcher.run()
    """

    def __init__(self, stream: EventStream) -> None:
        self._stream = stream
        self._handlers: dict[type[DssEvent], list[Handler]] = {}
        self._running = False

    def on(self, event_type: type[DssEvent]) -> Callable[[Handler], Handler]:
        """Decorator to register an async handler for an event type.

        Multiple handlers per type run concurrently. Handlers also receive
        events of subclasses if registered on a parent type (e.g. registering
        for :class:`DssEvent` catches everything).
        """
        def decorator(func: Handler) -> Handler:
            self._handlers.setdefault(event_type, []).append(func)
            return func
        return decorator

    def off(self, event_type: type[DssEvent], handler: Handler) -> None:
        """Remove a previously registered handler."""
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def run(self) -> None:
        """Iterate the stream and dispatch each event. Blocks until stop()."""
        self._running = True
        try:
            async for event in self._stream:
                if not self._running:
                    break
                await self._dispatch(event)
        finally:
            self._running = False

    async def stop(self) -> None:
        self._running = False
        await self._stream.stop()

    async def _dispatch(self, event: DssEvent) -> None:
        tasks: list[Awaitable[None]] = []
        for event_type, handlers in self._handlers.items():
            if isinstance(event, event_type):
                for h in handlers:
                    tasks.append(h(event))
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                _LOGGER.exception("[pydss.events] Handler raised: %s", r)
