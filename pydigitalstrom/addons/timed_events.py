"""``system-addon-timed-events`` wrapper.

Reads and writes the timed-events ("Zeitschaltuhr") entries Tom configures
via the dSS web UI. Pattern verified against entry 21 (Wecker / wake-up)
which Tom's ioBroker script writes daily.

Entry schema (see ``docs/DSS_API_NOTES.md`` section "Addon: system-addon-timed-events"):

- ``id``        : numeric string, e.g. ``"21"``
- ``name``      : display name
- ``scope``     : ``"system-addon-timed-events"`` (always)
- ``conditions`` : ``{"enabled": <bool>}``
- ``time``      : ``{timeBase, offset, recurrenceBase, recurrence}``
- ``actions``   : ``{"0": {<action0>}, "1": ...}`` - indexed object (NOT array)
- ``deleteCounter`` : monotonic counter, dSS-managed
- ``lastExecuted`` : ``"YYYY-MM-DD HH:MM:SS"`` or empty
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping

from ..exceptions import DssNotFoundError
from ..models import (
    TimeBase,
    TimedEvent,
    TimedEventRecurrence,
    TimedEventTime,
    UserActionAction,
    UserActionConditions,
    Weekday,
)
from .base import AddonBase

_LOGGER = logging.getLogger(__name__)


class TimedEventsAddon(AddonBase):
    """Wrapper around ``/scripts/system-addon-timed-events/entries/<id>``."""

    addon_name = "system-addon-timed-events"

    async def list_entries(self) -> list[TimedEvent]:
        """Load every timed-event entry as a typed :class:`TimedEvent`."""
        entry_ids = await self.list_entry_ids()
        out: list[TimedEvent] = []
        for entry_id in entry_ids:
            try:
                entry = await self.get_entry(entry_id)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("[pydss.addons.timed_events] entry %s load failed: %s", entry_id, exc)
                continue
            if entry is not None:
                out.append(entry)
        return out

    async def get_entry(self, entry_id: str) -> TimedEvent | None:
        """Load and parse a single entry."""
        try:
            raw = await self.load_entry_raw(entry_id)
        except DssNotFoundError:
            return None
        return parse_timed_event(entry_id, raw)

    async def save_entry(
        self,
        entry: TimedEvent,
        *,
        wait_for_reply: bool = True,
        reply_timeout_s: float = 5.0,
    ) -> str | None:
        """Create or update a timed-event.

        On create, set ``entry.entry_id`` to an empty string - the dSS
        will assign one (sequence-style, like scene-responder).

        Returns the entry id on success, or None on reply timeout.
        """
        payload = serialize_timed_event(entry)
        reply = await self.raise_config(
            action="save",
            value=payload,
            wait_for_reply=wait_for_reply,
            reply_timeout_s=reply_timeout_s,
        )
        if not wait_for_reply:
            return None
        if reply is None:
            _LOGGER.warning("[pydss.addons.timed_events] save: no reply within timeout")
            return None
        if reply.get("complete") != "success":
            _LOGGER.warning("[pydss.addons.timed_events] save reply: %s", dict(reply))

        if not entry.entry_id:
            # Look up the new id by name
            after = await self.list_entries()
            for e in after:
                if e.name == entry.name:
                    return e.entry_id
            return None
        return entry.entry_id

    async def delete_entry(self, entry_id: str) -> None:
        """Delete a timed-event by id via property-tree removal."""
        from ..exceptions import DssNotFoundError, DssProtocolError
        path = f"/scripts/system-addon-timed-events/entries/{entry_id}"
        try:
            await self._client.get("/json/property/remove", params={"path": path})
            _LOGGER.info("[pydss.addons.timed_events] removed %s", path)
        except DssProtocolError as exc:
            msg = str(exc).lower()
            if "could not find" in msg or "not found" in msg:
                raise DssNotFoundError(
                    f"[pydss.addons.timed_events] no entry {entry_id!r}"
                ) from exc
            raise


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_timed_event(entry_id: str, raw: Mapping[str, Any]) -> TimedEvent:
    """Convert the raw property-tree dict into a :class:`TimedEvent`."""
    conditions_raw = raw.get("conditions") or {}
    conditions = UserActionConditions(
        enabled=bool(conditions_raw.get("enabled", True)),
    )

    time_raw = raw.get("time") or {}
    time = TimedEventTime(
        time_base=TimeBase.from_raw(time_raw.get("timeBase")),
        offset_seconds=_to_int(time_raw.get("offset"), default=0),
        recurrence_base=str(time_raw.get("recurrenceBase", "")),
        recurrence=_parse_recurrence(time_raw.get("recurrence")),
    )

    actions = _parse_indexed_actions(raw.get("actions"))

    return TimedEvent(
        entry_id=str(raw.get("id", entry_id)),
        name=str(raw.get("name", "")),
        scope=str(raw.get("scope", "system-addon-timed-events")),
        conditions=conditions,
        time=time,
        actions=actions,
        delete_counter=_to_int(raw.get("deleteCounter"), default=0),
        last_executed=_parse_dt(raw.get("lastExecuted")),
    )


def _parse_recurrence(raw: Any) -> TimedEventRecurrence:
    """Recurrence is an indexed-object: ``{"0":"MO","1":"TU",...}``."""
    if not isinstance(raw, Mapping):
        return TimedEventRecurrence()
    weekdays: list[Weekday] = []
    for key in sorted(raw.keys()):
        value = raw[key]
        if isinstance(value, str):
            try:
                weekdays.append(Weekday(value))
            except ValueError:
                continue
    return TimedEventRecurrence(weekdays=tuple(weekdays))


def _parse_indexed_actions(raw: Any) -> tuple[UserActionAction, ...]:
    """Actions are stored as ``{"0": {...}, "1": {...}}`` - convert to ordered tuple."""
    if not isinstance(raw, Mapping):
        return ()
    actions: list[UserActionAction] = []
    for key in sorted(raw.keys(), key=_int_key):
        value = raw[key]
        if isinstance(value, Mapping):
            actions.append(UserActionAction.from_raw(value))
    return tuple(actions)


# ---------------------------------------------------------------------------
# Serialization (for save)
# ---------------------------------------------------------------------------


def serialize_timed_event(entry: TimedEvent) -> dict[str, Any]:
    """Build the JSON payload the dSS save-event expects.

    Matches Tom's ioBroker wake-up script (verified against entry 21):

    .. code-block:: json

        {
          "name": "Wecker einschalten",
          "id": "21",
          "time": {"offset": 29700, "timeBase": "daily"},
          "recurrence": {
            "timeArray": ["MO","TU","WE","TH","FR","SA","SU"],
            "recurrenceBase": "weekly"
          },
          "actions": [{"0":[{"type":[]},...], "type":"custom-event",
                       "event":"...", "delay":0, "category":"manual"}],
          "conditions": {"enabled": true}
        }

    Key save-vs-read differences:

    - ``recurrence`` lives at the top level (NOT nested in ``time``)
    - ``recurrence.timeArray`` is the weekday list (read uses indexed-dict
      ``recurrence.{"0":"MO","1":"TU",...}``)
    - ``actions`` is a JSON array, each element with an extra numeric-key
      placeholder field (same pattern as scene-responder)
    - ``conditions`` stays simple (``{enabled: bool}``) - unlike user-actions
      and scene-responder which use camelCase with null-defaults
    - When creating, ``id`` should be empty / None so the dSS assigns one
    """
    return {
        "name": entry.name,
        "id": entry.entry_id or None,
        "time": {
            "offset": entry.time.offset_seconds,
            "timeBase": entry.time.time_base.value,
        },
        "recurrence": {
            "timeArray": [d.value for d in entry.time.recurrence.weekdays],
            "recurrenceBase": entry.time.recurrence_base or "weekly",
        },
        "actions": [
            _serialize_action_with_placeholder(a, i)
            for i, a in enumerate(entry.actions)
        ],
        "conditions": {"enabled": entry.conditions.enabled},
    }


def _serialize_action_with_placeholder(action: UserActionAction, index: int) -> dict[str, Any]:
    """Action payload with the index-keyed schema-placeholder field.

    Same pattern as :func:`scene_responder._serialize_action_with_placeholder`:
    derive the placeholder dynamically from the actual fields emitted, so
    the two never drift out of sync.
    """
    body: dict[str, Any] = {"type": action.action_type.value}
    optional_map = {
        "dsuid": action.dsuid,
        "zone": action.zone,
        "group": action.group,
        "scene": action.scene,
        "force": action.force,
        "url": action.url,
        "statename": action.statename,
        "state": action.state,
        "addon-id": action.addon_id,
        "event": action.event,
    }
    for k, v in optional_map.items():
        if v is not None:
            body[k] = v
    body["delay"] = action.delay
    body["category"] = action.category

    placeholder = [{k: []} for k in body.keys()]
    return {str(index): placeholder, **body}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_int(v: Any, *, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _parse_dt(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _int_key(s: str) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0
