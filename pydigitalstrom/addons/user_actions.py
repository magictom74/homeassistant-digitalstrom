"""User-defined-actions wrapper.

User-actions live under ``/usr/events/<id>`` (NOT under
``/scripts/system-addon-user-defined-actions`` - the addon path holds only
the metadata container). Entries have ``source ==
"system-addon-user-defined-actions"`` which lets us filter them apart from
other ``/usr/events`` entries.

Triggering a user-action does NOT go through the addon's config event -
it uses the generic ``highlevelevent`` mechanism with ``actionName=<NAME>``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping

from ..exceptions import DssNotFoundError
from ..models import (
    UserAction,
    UserActionAction,
    UserActionConditions,
)
from .base import AddonBase

_LOGGER = logging.getLogger(__name__)


class UserActionsAddon(AddonBase):
    """Wrapper around ``/usr/events/<id>`` (filtered to user-defined-actions).

    Uses the **hyphen** convention for config/saved event names
    (``-config`` / ``-saved``), unlike other addons that use a dot
    (verified empirically against Tom's box 2026-05-30).
    """

    addon_name = "system-addon-user-defined-actions"

    USER_ACTION_SOURCE = "system-addon-user-defined-actions"
    """Filter for entries that are user-defined-actions.

    The dSS stores other event types in ``/usr/events`` too (e.g. sensor-event
    auto-generated entries). Only entries with this exact ``source`` value
    are user-actions we can manage.
    """

    @property
    def entries_root(self) -> str:
        return "/usr/events"

    @property
    def config_event_name(self) -> str:
        # HYPHEN, not dot - confirmed via DevTools capture 2026-05-30
        return f"{self.addon_name}-config"

    @property
    def saved_event_name(self) -> str:
        # HYPHEN, not dot - confirmed via DevTools capture 2026-05-30
        return f"{self.addon_name}-saved"

    async def list_actions(self) -> list[UserAction]:
        """Load all user-defined-actions.

        Other ``/usr/events`` entries (auto-generated, system-managed) are
        filtered out by source.
        """
        entry_ids = await self.list_entry_ids()
        out: list[UserAction] = []
        for entry_id in entry_ids:
            try:
                raw = await self.load_entry_raw(entry_id)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("[pydss.addons.user_actions] entry %s load failed: %s", entry_id, exc)
                continue
            action = parse_user_action(entry_id, raw)
            if action.is_user_defined:
                out.append(action)
        return out

    async def get_action(self, action_id: str) -> UserAction | None:
        try:
            raw = await self.load_entry_raw(action_id)
        except DssNotFoundError:
            return None
        return parse_user_action(action_id, raw)

    async def find_by_name(self, name: str) -> UserAction | None:
        """Reverse-lookup an action by its display name.

        Tom's box has 69 user-actions - linear scan is fine.
        """
        actions = await self.list_actions()
        for action in actions:
            if action.name == name:
                return action
        return None

    async def trigger_by_name(self, name: str) -> None:
        """Fire a user-action by its display name.

        Mechanism: ``event/raise?name=highlevelevent&parameter=actionName=<NAME>``.
        The dSS executes the action's ``actions[]`` if its conditions hold.
        """
        await self._client.get(
            "/json/event/raise",
            params={
                "name": "highlevelevent",
                "parameter": f"actionName={name}",
                "force": "true",
            },
        )
        _LOGGER.info("[pydss.addons.user_actions] Triggered %r", name)

    async def trigger_by_id(self, action_id: str) -> None:
        """Fire a user-action by its numeric id (resolves to name first)."""
        action = await self.get_action(action_id)
        if action is None:
            raise DssNotFoundError(f"[pydss.addons.user_actions] action {action_id} not found")
        await self.trigger_by_name(action.name)

    async def save_action(
        self,
        action: UserAction,
        *,
        wait_for_reply: bool = True,
        reply_timeout_s: float = 5.0,
    ) -> str | None:
        """Create or update a user-action.

        On create, set ``action.action_id`` to an empty string - the dSS
        assigns a new id automatically (Unix-timestamp-style). Pass an
        existing id to update.

        Args:
            action: The user-action to save.
            wait_for_reply: If True (default), waits for the addon's
                ``system-addon-user-defined-actions-saved`` reply event
                to confirm success. If False, fire-and-forget.
            reply_timeout_s: Reply-poll timeout in seconds.

        Returns:
            The action id (existing or newly-assigned) on success, or
            None if wait_for_reply=False or the reply timed out.

        Raises:
            DssProtocolError: If the dSS reports save failure.
        """
        payload = serialize_user_action_for_save(action)
        reply = await self.raise_config(
            action="save",
            value=payload,
            wait_for_reply=wait_for_reply,
            reply_timeout_s=reply_timeout_s,
        )
        if not wait_for_reply:
            return None
        if reply is None:
            _LOGGER.warning("[pydss.addons.user_actions] save: no reply within timeout")
            return None
        if reply.get("complete") != "success":
            _LOGGER.warning("[pydss.addons.user_actions] save reply: %s", dict(reply))

        # The reply does not include the new id - re-list and find by name.
        if not action.action_id:
            actions = await self.list_actions()
            for a in actions:
                if a.name == action.name:
                    return a.action_id
            return None
        return action.action_id

    async def delete_action(self, action_id: str) -> None:
        """Delete a user-action by id via direct property-tree removal.

        Uses ``/json/property/remove`` which is the only verb confirmed to
        actually purge an entry. Removes BOTH potential storage paths
        (the addon's mirror and ``/usr/events``) so the entry is fully gone.

        Verified live against the dSS - the property tree removal succeeds
        and the entry no longer appears in :meth:`list_actions`.

        Raises:
            DssNotFoundError: If the entry did not exist.
        """
        # Remove the primary entry under the addon's mirror
        addon_path = f"/scripts/system-addon-user-defined-actions/{action_id}"
        usr_path = f"/usr/events/{action_id}"
        removed_any = False

        for path in (addon_path, usr_path):
            try:
                await self._client.get("/json/property/remove", params={"path": path})
                removed_any = True
                _LOGGER.info("[pydss.addons.user_actions] removed %s", path)
            except DssNotFoundError:
                pass
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "could not find" in msg or "not found" in msg:
                    continue
                raise

        if not removed_any:
            raise DssNotFoundError(
                f"[pydss.addons.user_actions] no entry {action_id!r} to delete"
            )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_user_action(action_id: str, raw: Mapping[str, Any]) -> UserAction:
    conditions = _parse_conditions(raw.get("conditions"))
    actions = _parse_indexed_actions(raw.get("actions"))

    return UserAction(
        action_id=str(raw.get("id", action_id)),
        name=str(raw.get("name", "")),
        source=str(raw.get("source", "")),
        disabled=bool(raw.get("disabled", False)),
        last_saved=_parse_epoch_ms(raw.get("lastSaved")),
        last_executed=_parse_dt(raw.get("lastExecuted")),
        conditions=conditions,
        actions=actions,
    )


def _parse_conditions(raw: Any) -> UserActionConditions:
    if not isinstance(raw, Mapping):
        return UserActionConditions()

    system_states_raw = raw.get("states")
    system_states: dict[str, str] = {}
    if isinstance(system_states_raw, Mapping):
        for k, v in system_states_raw.items():
            system_states[str(k)] = str(v)

    addon_states_raw = raw.get("addon-states")
    addon_states: dict[str, dict[str, int]] = {}
    if isinstance(addon_states_raw, Mapping):
        for addon_id, state_map in addon_states_raw.items():
            if isinstance(state_map, Mapping):
                addon_states[str(addon_id)] = {
                    str(sk): _to_int(sv)
                    for sk, sv in state_map.items()
                }

    return UserActionConditions(
        enabled=bool(raw.get("enabled", True)),
        system_states=system_states,
        addon_states=addon_states,
    )


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
# Serialization (write)
# ---------------------------------------------------------------------------


def serialize_user_action(action: UserAction) -> dict[str, Any]:
    """Legacy save-payload format (kebab-case, with disabled field).

    Kept for compatibility - matches the property-tree READ shape. Use
    :func:`serialize_user_action_for_save` for actual save calls.
    """
    payload: dict[str, Any] = {
        "id": action.action_id,
        "name": action.name,
        "source": action.source or UserActionsAddon.USER_ACTION_SOURCE,
        "disabled": action.disabled,
        "conditions": {
            "enabled": action.conditions.enabled,
        },
        "actions": [_serialize_action(a) for a in action.actions],
    }
    if action.conditions.system_states:
        payload["conditions"]["states"] = dict(action.conditions.system_states)
    if action.conditions.addon_states:
        payload["conditions"]["addon-states"] = {
            k: dict(v) for k, v in action.conditions.addon_states.items()
        }
    return payload


def serialize_user_action_for_save(action: UserAction) -> dict[str, Any]:
    """Build the save-payload in the format the dSS web UI uses.

    Verified via DevTools capture 2026-05-30. Key differences vs the
    property-tree read shape:

    - ``id`` is sent as null when creating (system assigns)
    - ``source`` is sent as null (system assigns)
    - No ``disabled`` field
    - ``actions`` is a JSON array (not an indexed object)
    - ``conditions`` uses camelCase (``zoneState``, ``systemState``,
      ``addonState``) with all-null defaults
    """
    payload: dict[str, Any] = {
        "name": action.name,
        "id": action.action_id or None,
        "source": action.source or None,
        "actions": [_serialize_action_for_save(a) for a in action.actions],
        "conditions": {
            "enabled": action.conditions.enabled if action.conditions.enabled else None,
            "weekdays": None,
            "timeframe": None,
            "zoneState": _states_to_save_shape(action.conditions.system_states),
            "systemState": None,
            "addonState": _addon_states_to_save_shape(action.conditions.addon_states),
        },
    }
    return payload


def _serialize_action_for_save(action: UserActionAction) -> dict[str, Any]:
    """Lean per-action payload as used by the dSS web UI.

    Only emits fields that the action type actually needs - matches the
    captured ``{"type":"zone-blink","zone":9,"group":1,"delay":0}`` shape.
    """
    payload: dict[str, Any] = {"type": action.action_type.value}
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
        "delay": action.delay,
    }
    for k, v in optional_map.items():
        if v is not None:
            payload[k] = v
    return payload


def _states_to_save_shape(states: Any) -> Any:
    """system_states dict -> save format (or None if empty)."""
    if not states:
        return None
    return dict(states)


def _addon_states_to_save_shape(addon_states: Any) -> Any:
    """addon_states dict -> save format (or None if empty)."""
    if not addon_states:
        return None
    return {k: dict(v) for k, v in addon_states.items()}


def _serialize_action(action: UserActionAction) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": action.action_type.value,
        "delay": action.delay,
        "category": action.category,
    }
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
            payload[k] = v
    return payload


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


def _parse_epoch_ms(v: Any) -> datetime | None:
    """``lastSaved`` is stored as an epoch-millisecond string."""
    if v is None or v == "":
        return None
    try:
        ms = int(v)
    except (ValueError, TypeError):
        return None
    return datetime.fromtimestamp(ms / 1000.0)


def _int_key(s: str) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0
