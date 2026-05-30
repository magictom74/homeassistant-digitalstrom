"""``system-addon-scene-responder`` wrapper.

Scene responders react to bus events (button presses, state changes, scenes
called) and execute follow-up actions. Tom's box has 102 entries - 83
user-managed plus 19 system-generated (motion-detector responders with
``md_*`` prefix, fire-alarm reset, etc.).

Entry schema (verified against Tom's box, ``archive/scripts_addons.json``)::

    {
      "id":                "17",
      "name":              "Automat Beleuchtung Sitzplatz Sued ausschalten",
      "scope":             "system-addon-scene-responder",
      "technicalRole":     "system" | null,
      "persistentScope":   true,
      "delay":             0,
      "conditions":        {"enabled": true},
      "triggers":          {"1": {<trigger>}, "2": ...},
      "actions":           {"0": {<action>}, "1": ...},
      "singularTriggered": false,
      "initialTriggered":  false,
      "lastExecuted":      "2026-05-28 21:14:04"
    }

The addon uses the **dot** convention for config/saved events
(``system-addon-scene-responder.config`` / ``.saved``).

System-managed entries (technicalRole=='system' or md_* prefix) should not
be edited - this module filters them out of :meth:`list_entries` by default.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping

from ..exceptions import DssNotFoundError, DssProtocolError
from ..models import (
    ResponderTrigger,
    SceneResponder,
    UserActionAction,
    UserActionConditions,
)
from .base import AddonBase

_LOGGER = logging.getLogger(__name__)


class SceneResponderAddon(AddonBase):
    """Wrapper around ``/scripts/system-addon-scene-responder/entries/<id>``."""

    addon_name = "system-addon-scene-responder"

    async def list_entries(self, *, include_system: bool = False) -> list[SceneResponder]:
        """Load every scene-responder entry.

        Args:
            include_system: If True, also returns system-managed entries
                (``md_*`` motion-detector responders, fire-alarm resets,
                ``technicalRole=='system'``). Default False - those entries
                should not be edited by users.
        """
        entry_ids = await self.list_entry_ids()
        out: list[SceneResponder] = []
        for entry_id in entry_ids:
            try:
                raw = await self.load_entry_raw(entry_id)
            except DssNotFoundError:
                continue
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("[pydss.addons.scene_responder] entry %s load failed: %s", entry_id, exc)
                continue
            entry = parse_scene_responder(entry_id, raw)
            if not include_system and entry.is_system_managed:
                continue
            out.append(entry)
        return out

    async def get_entry(self, entry_id: str) -> SceneResponder | None:
        try:
            raw = await self.load_entry_raw(entry_id)
        except DssNotFoundError:
            return None
        return parse_scene_responder(entry_id, raw)

    async def save_entry(
        self,
        entry: SceneResponder,
        *,
        wait_for_reply: bool = True,
        reply_timeout_s: float = 5.0,
    ) -> str | None:
        """Create or update a scene-responder entry.

        On create, set ``entry.entry_id`` to an empty string - the dSS
        assigns a new id. On update, pass the existing id.

        Returns:
            The entry id on success, or None if the save reply timed out.
        """
        payload = serialize_scene_responder_for_save(entry)
        reply = await self.raise_config(
            action="save",
            value=payload,
            wait_for_reply=wait_for_reply,
            reply_timeout_s=reply_timeout_s,
        )
        if not wait_for_reply:
            return None
        if reply is None:
            _LOGGER.warning("[pydss.addons.scene_responder] save: no reply within timeout")
            return None
        if reply.get("complete") != "success":
            _LOGGER.warning("[pydss.addons.scene_responder] save reply: %s", dict(reply))

        if not entry.entry_id:
            # Re-list and look up by name to find the new id
            after = await self.list_entries(include_system=True)
            for e in after:
                if e.name == entry.name:
                    return e.entry_id
            return None
        return entry.entry_id

    async def delete_entry(self, entry_id: str) -> None:
        """Delete a scene-responder entry by id.

        Uses ``property/remove`` which directly purges the subtree.
        """
        path = f"/scripts/system-addon-scene-responder/entries/{entry_id}"
        try:
            await self._client.get("/json/property/remove", params={"path": path})
            _LOGGER.info("[pydss.addons.scene_responder] removed %s", path)
        except DssProtocolError as exc:
            msg = str(exc).lower()
            if "could not find" in msg or "not found" in msg:
                raise DssNotFoundError(
                    f"[pydss.addons.scene_responder] no entry {entry_id!r}"
                ) from exc
            raise

    async def set_enabled(
        self,
        entry_id: str,
        enabled: bool,
        *,
        wait_for_reply: bool = True,
    ) -> str | None:
        """Convenience: flip an entry's ``conditions.enabled`` flag.

        Loads the entry, replaces enabled, saves it back. Returns the entry
        id on success.
        """
        entry = await self.get_entry(entry_id)
        if entry is None:
            raise DssNotFoundError(
                f"[pydss.addons.scene_responder] no entry {entry_id!r} to enable/disable"
            )
        from dataclasses import replace
        new_conditions = UserActionConditions(
            enabled=enabled,
            system_states=entry.conditions.system_states,
            addon_states=entry.conditions.addon_states,
        )
        new_entry = replace(entry, conditions=new_conditions)
        return await self.save_entry(new_entry, wait_for_reply=wait_for_reply)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_scene_responder(entry_id: str, raw: Mapping[str, Any]) -> SceneResponder:
    """Convert the raw property-tree dict into a :class:`SceneResponder`."""
    conditions_raw = raw.get("conditions") or {}
    conditions = UserActionConditions(
        enabled=bool(conditions_raw.get("enabled", True)),
    )

    triggers = _parse_indexed_triggers(raw.get("triggers"))
    actions = _parse_indexed_actions(raw.get("actions"))

    return SceneResponder(
        entry_id=str(raw.get("id", entry_id)),
        name=str(raw.get("name", "")),
        scope=str(raw.get("scope", "system-addon-scene-responder")),
        technical_role=raw.get("technicalRole"),
        persistent_scope=bool(raw.get("persistentScope", True)),
        delay=_to_int(raw.get("delay"), default=0),
        conditions=conditions,
        triggers=triggers,
        actions=actions,
        singular_triggered=bool(raw.get("singularTriggered", False)),
        initial_triggered=bool(raw.get("initialTriggered", False)),
        last_executed=_parse_dt(raw.get("lastExecuted")),
    )


def _parse_indexed_triggers(raw: Any) -> tuple[ResponderTrigger, ...]:
    if not isinstance(raw, Mapping):
        return ()
    triggers: list[ResponderTrigger] = []
    for key in sorted(raw.keys(), key=_int_key):
        value = raw[key]
        if isinstance(value, Mapping):
            triggers.append(ResponderTrigger.from_raw(value))
    return tuple(triggers)


def _parse_indexed_actions(raw: Any) -> tuple[UserActionAction, ...]:
    if not isinstance(raw, Mapping):
        return ()
    actions: list[UserActionAction] = []
    for key in sorted(raw.keys(), key=_int_key):
        value = raw[key]
        if isinstance(value, Mapping):
            actions.append(UserActionAction.from_raw(value))
    return tuple(actions)


# ---------------------------------------------------------------------------
# Serialization (save)
# ---------------------------------------------------------------------------


def serialize_scene_responder_for_save(entry: SceneResponder) -> dict[str, Any]:
    """Build the save-payload in the format the dSS web UI uses.

    Verified via DevTools capture 2026-05-30 (entry 27 "Alarm Feuer" save).

    Notable specifics vs other addons:

    - ``actions`` are emitted as JSON array, but each action gets an extra
      numeric-key field (``"0"``, ``"1"``, ``"2"``, ...) containing a
      schema-placeholder list of single-key empty-array dicts. This pattern
      also appears in Tom's ioBroker timed-events save script.
    - ``conditions.systemState`` is a list of ``{name, value}`` objects
      (not a dict keyed by state-name).
    - ``conditions.addonState`` is a list of ``{scriptID, name, value}``.
    - No ``source``, ``scope``, ``technicalRole``, ``persistentScope`` fields
      in the save payload - the dSS sets them server-side.
    - ``initialTriggered`` is not sent either - server default.
    """
    payload: dict[str, Any] = {
        "name": entry.name,
        "id": entry.entry_id or None,
        "triggers": [_serialize_trigger(t) for t in entry.triggers],
        "delay": entry.delay,
        "singularTriggered": entry.singular_triggered,
        "actions": [
            _serialize_action_with_placeholder(a, i)
            for i, a in enumerate(entry.actions)
        ],
        "conditions": {
            "enabled": entry.conditions.enabled,
            "weekdays": None,
            "timeframe": None,
            "zoneState": None,
            "systemState": _system_states_to_list(entry.conditions.system_states),
            "addonState": _addon_states_to_list(entry.conditions.addon_states),
        },
    }
    return payload


def _serialize_trigger(trigger: ResponderTrigger) -> dict[str, Any]:
    """Triggers are plain JSON objects - no placeholder pattern."""
    payload: dict[str, Any] = {"type": trigger.trigger_type.value}
    optional_map = {
        "addon-id": trigger.addon_id,
        "name": trigger.name,
        "state": trigger.state,
        "dsuid": trigger.dsuid,
        "msg": trigger.msg,
        "buttonIndex": trigger.button_index,
        "zone": trigger.zone,
        "group": trigger.group,
        "scene": trigger.scene,
        "event": trigger.event,
    }
    for k, v in optional_map.items():
        if v is not None:
            payload[k] = v
    return payload


def _serialize_action_with_placeholder(action: UserActionAction, index: int) -> dict[str, Any]:
    """Action payload with the index-keyed schema-hint field.

    Each action emits something like::

        {
          "0": [{"type":[]},{"event":[]},{"delay":[]},{"category":[]}],
          "type": "custom-event",
          "event": "1234",
          "delay": 0,
          "category": "manual"
        }

    The placeholder lists exactly the keys that follow, each as a
    single-key dict with an empty array. We derive it dynamically from
    the actual fields we emit so the two always match.
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


def _system_states_to_list(states: Any) -> list[dict[str, Any]] | None:
    """Map ``{name: value}`` dict to ``[{name, value}, ...]`` list."""
    if not states:
        return None
    return [{"name": k, "value": v} for k, v in states.items()]


def _addon_states_to_list(addon_states: Any) -> list[dict[str, Any]] | None:
    """Flatten ``{addon-id: {name: value}}`` to ``[{scriptID, name, value}, ...]``."""
    if not addon_states:
        return None
    out: list[dict[str, Any]] = []
    for addon_id, state_map in addon_states.items():
        if isinstance(state_map, Mapping):
            for name, value in state_map.items():
                out.append({"scriptID": addon_id, "name": name, "value": value})
    return out or None


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
