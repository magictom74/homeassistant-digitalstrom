"""Alarm framework: rule-based event-driven alerting.

Three observation sources feed the rule engine:

- **Circuit readings** from :class:`CircuitMonitor` (power outage detection)
- **dSS event stream** from :class:`EventStream` (motion, sensor errors, state changes)
- **dSS state polling** via ``state/get`` (presence, custom-states, boilermode)

Rules implement the :class:`AlarmRule` protocol and decide on every tick
whether to fire (raise an :class:`AlarmEvent`) or clear an active alarm.

Built-in rules cover the use-cases Tom listed:

- :class:`CircuitOutageRule` - breaker tripped or dSM unreachable
- :class:`MotionWhileAbsentRule` - motion event while presence-state = absent
- :class:`ZoneTemperatureLowRule` - frost warning per zone
- :class:`SensorOutageRule` - sensor stopped reporting (zoneSensorError or stale age)
- :class:`UserStateAlarmRule` - generic: state X reaches value Y -> alarm

The engine is HA-agnostic. The HA integration consumes :class:`AlarmEvent`
instances and creates ``persistent_notification`` / ``binary_sensor`` entities
from them.
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Protocol

from .circuits import CircuitMonitor, CircuitReading, CircuitState
from .client import DssClient
from .events import (
    DeviceBinaryInputEvent,
    DssEvent,
    EventStream,
    StateChangeEvent,
    ZoneSensorErrorEvent,
    ZoneSensorValueEvent,
)
from .models import SensorType

_LOGGER = logging.getLogger(__name__)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class AlarmEvent:
    """An alarm condition that fired or cleared.

    Emitted by :class:`AlarmEngine` as alarms transition between active and
    cleared. Subscribers receive one event per transition - they do NOT poll.
    """

    alarm_id: str
    """Stable id per (rule, target) - identifies the same alarm across fire+clear."""

    rule_name: str
    severity: Severity
    title: str
    description: str
    triggered_at: datetime
    cleared_at: datetime | None
    context: dict[str, Any]
    """Rule-specific extra data (e.g. dsuid for circuit alarms, zone_id for temp)."""

    @property
    def is_active(self) -> bool:
        return self.cleared_at is None


@dataclass(slots=True)
class AlarmContext:
    """Observation state passed to every rule on each evaluation.

    Rules read from this; the engine fills it before calling ``evaluate``.
    """

    circuit_readings: dict[str, CircuitReading] = field(default_factory=dict)
    """Most recent reading per circuit dSUID."""

    recent_events: deque[DssEvent] = field(default_factory=lambda: deque(maxlen=512))
    """Bounded queue of recent dSS events (newest at right)."""

    user_states: dict[str, str] = field(default_factory=dict)
    """Cached `/json/state/get` results keyed by state name."""

    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def latest_event(self, event_type: type[DssEvent] | None = None, name: str | None = None) -> DssEvent | None:
        """Return the most recent event matching either a type or a name."""
        for ev in reversed(self.recent_events):
            if event_type is not None and not isinstance(ev, event_type):
                continue
            if name is not None and ev.name != name:
                continue
            return ev
        return None

    def events_since(self, since: datetime, event_type: type[DssEvent] | None = None) -> list[DssEvent]:
        out: list[DssEvent] = []
        for ev in self.recent_events:
            if ev.received_at < since:
                continue
            if event_type is not None and not isinstance(ev, event_type):
                continue
            out.append(ev)
        return out


class AlarmRule(Protocol):
    """Rules are pure deciders. State is held by the engine."""

    @property
    def rule_name(self) -> str: ...

    @property
    def severity(self) -> Severity: ...

    @property
    def debounce_seconds(self) -> float:
        """Minimum time a triggered condition must persist before firing."""
        ...

    @abstractmethod
    def evaluate(self, ctx: AlarmContext) -> Iterable[_RuleDecision]:
        """Return zero or more decisions for this tick.

        A rule may track multiple targets (e.g. one alarm per circuit) - each
        yields its own :class:`_RuleDecision`.
        """
        ...


@dataclass(slots=True)
class _RuleDecision:
    """Internal: rule output before debounce + engine bookkeeping.

    A rule yields one decision per *target* (a circuit, a zone, a state, ...).
    The engine maps these to AlarmEvents.
    """

    target_id: str
    """Stable id per target (e.g. dsuid, zone-id, state-name)."""

    is_triggered: bool
    title: str = ""
    description: str = ""
    context: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------


@dataclass
class CircuitOutageRule:
    """Fires when a circuit transitions to OFFLINE or UNREACHABLE.

    Whitelist support: pass ``ignore_dsuids`` for circuits known to be
    intentionally offline (e.g. Tom's F6 has been dead for ages and that
    is expected).
    """

    rule_name: str = "circuit_outage"
    severity: Severity = Severity.CRITICAL
    debounce_seconds: float = 60.0
    """Wait at least this long before firing - avoids brief flickers during recovery."""

    ignore_dsuids: frozenset[str] = frozenset()
    """Circuits known to be offline on purpose (no alarm)."""

    def evaluate(self, ctx: AlarmContext) -> list[_RuleDecision]:
        out: list[_RuleDecision] = []
        for dsuid, reading in ctx.circuit_readings.items():
            if reading.state is CircuitState.VIRTUAL:
                continue  # virtual circuits have no power
            if dsuid in self.ignore_dsuids:
                continue
            if reading.is_outage:
                out.append(_RuleDecision(
                    target_id=dsuid,
                    is_triggered=True,
                    title=f"Circuit '{reading.name}' offline",
                    description=(
                        f"{reading.name} is {reading.state.value} "
                        f"(state={reading.raw_state}, present={reading.raw_present}). "
                        f"Check breaker."
                    ),
                    context={
                        "dsuid": dsuid,
                        "name": reading.name,
                        "state": reading.state.value,
                        "raw_state": reading.raw_state,
                    },
                ))
            else:
                out.append(_RuleDecision(target_id=dsuid, is_triggered=False))
        return out


@dataclass
class MotionWhileAbsentRule:
    """Fires when a motion event arrives while presence-state = absent.

    The dSS user-defined-state ``presence`` typically holds ``"present"`` or
    ``"absent"``. Customize via ``presence_state_name`` and ``absent_value``.
    """

    rule_name: str = "motion_while_absent"
    severity: Severity = Severity.CRITICAL
    debounce_seconds: float = 0.0
    """Motion events are atomic - fire immediately."""

    presence_state_name: str = "presence"
    absent_value: str = "absent"
    motion_input_type: int = 1
    """``deviceBinaryInputEvent.inputType`` value indicating motion. dSS default = 1."""

    window_seconds: float = 30.0
    """Consider motion events from the last N seconds."""

    def evaluate(self, ctx: AlarmContext) -> list[_RuleDecision]:
        if ctx.user_states.get(self.presence_state_name) != self.absent_value:
            return [_RuleDecision(target_id="global", is_triggered=False)]

        cutoff = ctx.now - timedelta(seconds=self.window_seconds)
        recent_motion = [
            ev for ev in ctx.events_since(cutoff, DeviceBinaryInputEvent)
            if ev.input_type == self.motion_input_type and ev.input_state == 1
        ]
        if not recent_motion:
            return [_RuleDecision(target_id="global", is_triggered=False)]

        ev = recent_motion[-1]
        return [_RuleDecision(
            target_id=f"motion-{ev.dsid}",
            is_triggered=True,
            title="Motion detected while absent",
            description=f"Motion sensor {ev.dsid} fired in zone {ev.source.zone_id} - presence={self.absent_value}.",
            context={
                "dsid": ev.dsid,
                "zone_id": ev.source.zone_id,
                "input_index": ev.input_index,
                "presence_state": self.absent_value,
            },
        )]


@dataclass
class ZoneTemperatureLowRule:
    """Fires when a zone temperature drops below threshold.

    Source: ``zoneSensorValue`` events with ``sensorType=9`` (room temp).
    """

    rule_name: str = "zone_temperature_low"
    severity: Severity = Severity.WARNING
    debounce_seconds: float = 300.0

    zone_id: int | None = None
    """If set, only this zone. None = all zones."""

    min_celsius: float = 5.0
    """Trigger when value < this."""

    sensor_type: int = int(SensorType.TEMP_ROOM)
    """Override if you're using a different sensor type (e.g. boiler temp)."""

    def evaluate(self, ctx: AlarmContext) -> list[_RuleDecision]:
        # Use latest reading per zone
        latest_per_zone: dict[int, ZoneSensorValueEvent] = {}
        for ev in ctx.recent_events:
            if not isinstance(ev, ZoneSensorValueEvent):
                continue
            if ev.sensor_type_raw != self.sensor_type:
                continue
            if self.zone_id is not None and ev.zone_id != self.zone_id:
                continue
            latest_per_zone[ev.zone_id] = ev

        out: list[_RuleDecision] = []
        for zone_id, ev in latest_per_zone.items():
            target = f"zone-{zone_id}-sensor-{self.sensor_type}"
            value = ev.sensor_value_float
            if value < self.min_celsius:
                out.append(_RuleDecision(
                    target_id=target,
                    is_triggered=True,
                    title=f"Zone {zone_id} temperature low",
                    description=f"Zone {zone_id} reads {value:.1f}°C (threshold {self.min_celsius}°C).",
                    context={"zone_id": zone_id, "value_celsius": value, "threshold": self.min_celsius},
                ))
            else:
                out.append(_RuleDecision(target_id=target, is_triggered=False))
        return out


@dataclass
class SensorOutageRule:
    """Fires on ``zoneSensorError`` or when no value for too long.

    The dSS emits ``zoneSensorError`` with ``lastValueTS=1970-01-01...`` when
    a sensor has never reported. Useful as the "UMK signal lost" alarm Tom
    mentioned for boiler / floor heating / water sensors.
    """

    rule_name: str = "sensor_outage"
    severity: Severity = Severity.WARNING
    debounce_seconds: float = 60.0

    zone_id: int | None = None
    sensor_type: int | None = None

    def evaluate(self, ctx: AlarmContext) -> list[_RuleDecision]:
        out: list[_RuleDecision] = []
        seen: set[str] = set()
        for ev in reversed(ctx.recent_events):
            if not isinstance(ev, ZoneSensorErrorEvent):
                continue
            if self.zone_id is not None and ev.zone_id != self.zone_id:
                continue
            if self.sensor_type is not None and ev.sensor_type_raw != self.sensor_type:
                continue
            target = f"zone-{ev.zone_id}-sensor-{ev.sensor_type_raw}"
            if target in seen:
                continue
            seen.add(target)
            out.append(_RuleDecision(
                target_id=target,
                is_triggered=True,
                title=f"Sensor outage zone {ev.zone_id}",
                description=(
                    f"Zone {ev.zone_id} sensor type {ev.sensor_type_raw} has no recent value "
                    f"(last seen {ev.last_value_ts_iso or 'never'})."
                ),
                context={
                    "zone_id": ev.zone_id,
                    "sensor_type": ev.sensor_type_raw,
                    "last_value_ts": ev.last_value_ts_iso,
                },
            ))
        # Cleared decisions for previously-active targets are derived by engine bookkeeping
        return out


@dataclass
class UserStateAlarmRule:
    """Generic: fire when a user-state takes a specific value.

    Use for water alarm, boiler alarm, fire alarm - any dSS state where one
    value means "alert".
    """

    rule_name: str = "user_state"
    severity: Severity = Severity.CRITICAL
    debounce_seconds: float = 0.0

    state_name: str = ""
    alarm_values: frozenset[str] = frozenset()
    title_template: str = "State '{state_name}' is '{value}'"
    description_template: str = "User-state '{state_name}' reached alarm value '{value}'."

    def evaluate(self, ctx: AlarmContext) -> list[_RuleDecision]:
        value = ctx.user_states.get(self.state_name)
        target = f"state-{self.state_name}"
        if value is None:
            return [_RuleDecision(target_id=target, is_triggered=False)]
        if value in self.alarm_values:
            return [_RuleDecision(
                target_id=target,
                is_triggered=True,
                title=self.title_template.format(state_name=self.state_name, value=value),
                description=self.description_template.format(state_name=self.state_name, value=value),
                context={"state_name": self.state_name, "value": value},
            )]
        return [_RuleDecision(target_id=target, is_triggered=False)]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass
class _ActiveAlarm:
    decision: _RuleDecision
    first_seen: datetime
    fired: bool = False
    fired_at: datetime | None = None


class AlarmEngine:
    """Orchestrates rules + observation sources.

    Usage::

        engine = AlarmEngine(client)
        engine.add_rule(CircuitOutageRule(ignore_dsuids=frozenset({F6_DSUID})))
        engine.add_rule(WaterAlarmRule())

        async for alarm in engine.run():
            print(alarm.title, alarm.severity, alarm.is_active)

    The engine spawns three background tasks:

    1. Circuit polling (via :class:`CircuitMonitor`)
    2. dSS event stream (via :class:`EventStream`)
    3. State polling (configurable state names)

    Every ``tick_interval_s`` seconds it evaluates all rules and emits
    AlarmEvents for transitions (rising or falling edge after debounce).
    """

    def __init__(
        self,
        client: DssClient,
        *,
        circuit_monitor: CircuitMonitor | None = None,
        event_stream: EventStream | None = None,
        state_names_to_poll: Iterable[str] = (),
        state_poll_interval_s: float = 30.0,
        tick_interval_s: float = 5.0,
    ) -> None:
        self._client = client
        self._circuit_monitor = circuit_monitor or CircuitMonitor(client, poll_interval_s=30.0)
        self._event_stream = event_stream or EventStream(client)
        self._state_names = tuple(state_names_to_poll)
        self._state_poll_interval = state_poll_interval_s
        self._tick_interval = tick_interval_s

        self._rules: list[AlarmRule] = []
        self._context = AlarmContext()
        self._active: dict[tuple[str, str], _ActiveAlarm] = {}
        self._output: asyncio.Queue[AlarmEvent] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def add_rule(self, rule: AlarmRule) -> None:
        self._rules.append(rule)

    @property
    def context(self) -> AlarmContext:
        return self._context

    @property
    def rules(self) -> tuple[AlarmRule, ...]:
        return tuple(self._rules)

    async def run(self) -> AsyncIterator[AlarmEvent]:
        """Start background workers, yield :class:`AlarmEvent` as they fire/clear.

        Caller must `break` or call :meth:`stop` to terminate.
        """
        await self._start()
        try:
            while not self._stop.is_set():
                try:
                    ev = await asyncio.wait_for(self._output.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                yield ev
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._event_stream.stop()

    async def _start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="pydss-alarm-tick"),
            asyncio.create_task(self._circuit_loop(), name="pydss-alarm-circuits"),
            asyncio.create_task(self._event_loop(), name="pydss-alarm-events"),
            asyncio.create_task(self._state_loop(), name="pydss-alarm-states"),
        ]

    async def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._context.now = datetime.now(timezone.utc)
                self._evaluate_all_rules()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("[pydss.alarms] tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick_interval)
            except asyncio.TimeoutError:
                pass

    async def _circuit_loop(self) -> None:
        try:
            async for reading in self._circuit_monitor.stream():
                self._context.circuit_readings[reading.dsuid] = reading
                if self._stop.is_set():
                    return
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            _LOGGER.exception("[pydss.alarms] circuit loop failed")

    async def _event_loop(self) -> None:
        try:
            async with self._event_stream as stream:
                async for ev in stream:
                    self._context.recent_events.append(ev)
                    if self._stop.is_set():
                        return
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            _LOGGER.exception("[pydss.alarms] event loop failed")

    async def _state_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for name in self._state_names:
                    try:
                        result = await self._client.get(
                            "/json/state/get",
                            params={"name": name},
                        )
                        if isinstance(result, dict):
                            value = result.get("value")
                            if value is not None:
                                self._context.user_states[name] = str(value)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("[pydss.alarms] state get %s failed: %s", name, exc)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("[pydss.alarms] state loop iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._state_poll_interval)
            except asyncio.TimeoutError:
                pass

    def _evaluate_all_rules(self) -> None:
        seen_keys: set[tuple[str, str]] = set()
        now = self._context.now

        for rule in self._rules:
            try:
                decisions = list(rule.evaluate(self._context))
            except Exception:  # noqa: BLE001
                _LOGGER.exception("[pydss.alarms] rule %s evaluate failed", rule.rule_name)
                continue

            for decision in decisions:
                key = (rule.rule_name, decision.target_id)
                seen_keys.add(key)
                self._process_decision(rule, decision, key, now)

        # Auto-clear alarms whose rule did not yield a decision for them this tick
        # (rule may have removed the target).
        stale = [key for key in self._active if key not in seen_keys]
        for key in stale:
            rule_name, target_id = key
            rule = next((r for r in self._rules if r.rule_name == rule_name), None)
            if rule is None:
                self._active.pop(key, None)
                continue
            self._clear_alarm(rule, key, now, reason="no longer reported")

    def _process_decision(
        self,
        rule: AlarmRule,
        decision: _RuleDecision,
        key: tuple[str, str],
        now: datetime,
    ) -> None:
        existing = self._active.get(key)
        if decision.is_triggered:
            if existing is None:
                self._active[key] = _ActiveAlarm(decision=decision, first_seen=now)
                return
            # Update last decision (refresh description/context)
            existing.decision = decision
            if not existing.fired:
                elapsed = (now - existing.first_seen).total_seconds()
                if elapsed >= rule.debounce_seconds:
                    self._fire_alarm(rule, existing, key, now)
        else:
            if existing is not None:
                if existing.fired:
                    self._clear_alarm(rule, key, now, reason="condition resolved")
                self._active.pop(key, None)

    def _fire_alarm(
        self,
        rule: AlarmRule,
        active: _ActiveAlarm,
        key: tuple[str, str],
        now: datetime,
    ) -> None:
        active.fired = True
        active.fired_at = now
        event = AlarmEvent(
            alarm_id=_alarm_id(key),
            rule_name=rule.rule_name,
            severity=rule.severity,
            title=active.decision.title,
            description=active.decision.description,
            triggered_at=now,
            cleared_at=None,
            context=dict(active.decision.context),
        )
        self._output.put_nowait(event)
        _LOGGER.warning(
            "[pydss.alarms] FIRED %s [%s]: %s",
            rule.rule_name,
            rule.severity.value,
            event.title,
        )

    def _clear_alarm(
        self,
        rule: AlarmRule,
        key: tuple[str, str],
        now: datetime,
        *,
        reason: str,
    ) -> None:
        existing = self._active.pop(key, None)
        if existing is None or not existing.fired:
            return
        event = AlarmEvent(
            alarm_id=_alarm_id(key),
            rule_name=rule.rule_name,
            severity=rule.severity,
            title=existing.decision.title,
            description=f"Cleared: {reason}",
            triggered_at=existing.fired_at or now,
            cleared_at=now,
            context=dict(existing.decision.context),
        )
        self._output.put_nowait(event)
        _LOGGER.info(
            "[pydss.alarms] CLEARED %s [%s]: %s (%s)",
            rule.rule_name,
            rule.severity.value,
            existing.decision.title,
            reason,
        )


def _alarm_id(key: tuple[str, str]) -> str:
    rule, target = key
    return f"{rule}:{target}"
