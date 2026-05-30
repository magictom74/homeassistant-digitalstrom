"""Base class for system-addon wrappers.

All system-addons in the dSS follow the same pattern:

- **Config storage**: ``/scripts/<addon-name>/entries/<id>/...`` (property tree)
- **CRUD operations**: ``event/raise?name=<addon-name>.config&parameter=actions=<verb>;value=<JSON>``
- **Trigger / activation**: handled per-addon, often via ``highlevelevent``

This module factors out the shared pieces: walking the entries subtree,
serializing entries to the save-payload, raising the config event.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from ..client import DssClient
from ..exceptions import DssNotFoundError, DssProtocolError
from ..property import PropertyTreeWalker

_LOGGER = logging.getLogger(__name__)


class AddonBase:
    """Common functionality for all system-addon wrappers."""

    addon_name: str
    """Sub-path under ``/scripts/`` and prefix for the config event name."""

    entries_path: str = "entries"
    """Sub-path under ``/scripts/<addon-name>/`` where the entries live.

    Most addons use ``entries``; user-defined-states splits into multiple
    subtrees, those addons override this.
    """

    def __init__(self, client: DssClient) -> None:
        self._client = client
        self._walker = PropertyTreeWalker(client, max_depth=10)

    @property
    def base_path(self) -> str:
        return f"/scripts/{self.addon_name}"

    @property
    def entries_root(self) -> str:
        return f"{self.base_path}/{self.entries_path}"

    @property
    def config_event_name(self) -> str:
        """Name of the ``event/raise`` event that addons listen on for CRUD.

        Default uses the ``<addon>.config`` (dot) convention - the right one for
        most addons (system-addon-timed-events, system-addon-scene-responder,
        system-addon-user-defined-states). Some addons use a HYPHEN instead
        (system-addon-user-defined-actions). Override per-addon if needed.
        """
        return f"{self.addon_name}.config"

    @property
    def saved_event_name(self) -> str:
        """Reply event the addon emits after a successful save.

        Convention varies per addon. Default is ``<addon>.saved`` (dot).
        Override where the addon uses a hyphen instead.
        """
        return f"{self.addon_name}.saved"

    async def list_entry_ids(self) -> list[str]:
        """Return the list of entry IDs that exist under the entries-root.

        Sub-classes typically wrap this in a typed ``list_entries`` that also
        loads each entry's body.
        """
        children = await self._walker.get_children(self.entries_root)
        return [c.name for c in children]

    async def load_entry_raw(self, entry_id: str) -> Mapping[str, Any]:
        """Load one entry's full sub-tree as a nested dict.

        Returns the same shape as the discovery archive's
        ``scripts_addons.json``. Sub-classes parse this into typed dataclasses.

        Raises:
            DssNotFoundError: If the entry id does not exist (the dSS
                responds with ``Could not find node ...``).
        """
        path = f"{self.entries_root}/{entry_id}"
        try:
            tree = await self._walker.walk(path)
        except DssProtocolError as exc:
            if "Could not find node" in str(exc):
                raise DssNotFoundError(
                    f"[pydss.addons] No entry {entry_id!r} under {self.entries_root}"
                ) from exc
            raise
        if not tree:
            raise DssNotFoundError(
                f"[pydss.addons] No entry {entry_id!r} under {self.entries_root}"
            )
        return tree

    async def raise_config(
        self,
        *,
        action: str,
        value: Mapping[str, Any] | None = None,
        extra_params: Mapping[str, str] | None = None,
        wait_for_reply: bool = False,
        reply_timeout_s: float = 5.0,
    ) -> Mapping[str, Any] | None:
        """Fire the addon's config event - the universal write path.

        Args:
            action: Verb the addon implements, e.g. ``save`` or ``delete``.
            value: Optional payload, JSON-encoded into the ``value=`` segment.
                Most addons require a complete entry object here.
            extra_params: Additional parameters that go into the
                semicolon-separated ``parameter`` string after ``value``.
            wait_for_reply: If True, subscribes to :attr:`saved_event_name`
                BEFORE raising, then polls ``event/get`` until the reply
                arrives or ``reply_timeout_s`` elapses. Returns the reply
                event's ``properties`` dict, or None on timeout. This
                matches the dSS web UI's ``pushAndQuery`` pattern.
            reply_timeout_s: How long to wait for the reply (only used when
                wait_for_reply=True).

        Returns:
            The reply event properties (e.g. ``{"complete": "success"}``)
            if wait_for_reply=True, else None.
        """
        parts = [f"actions={action}"]
        if value is not None:
            parts.append("value=" + json.dumps(value, separators=(",", ":")))
        if extra_params:
            for k, v in extra_params.items():
                parts.append(f"{k}={v}")
        parameter = ";".join(parts)

        _LOGGER.info(
            "[pydss.addons.%s] raise config: action=%s (payload %d bytes)",
            self.addon_name,
            action,
            len(parameter),
        )

        if not wait_for_reply:
            await self._client.get(
                "/json/event/raise",
                params={"name": self.config_event_name, "parameter": parameter},
            )
            return None

        # pushAndQuery pattern: subscribe, raise, poll, unsubscribe
        import random as _random
        sub_id = _random.randint(10_000_000_000, 99_999_999_999)
        try:
            await self._client.get(
                "/json/event/subscribe",
                params={"subscriptionID": sub_id, "name": self.saved_event_name},
            )
            await self._client.get(
                "/json/event/raise",
                params={"name": self.config_event_name, "parameter": parameter},
            )
            timeout_ms = int(reply_timeout_s * 1000)
            events = await self._client.event_long_poll(sub_id, timeout_ms=timeout_ms)
            if events:
                first = events[0]
                if isinstance(first, dict):
                    return first.get("properties") or {}
            return None
        finally:
            try:
                await self._client.get(
                    "/json/event/unsubscribe",
                    params={"subscriptionID": sub_id, "name": self.saved_event_name},
                )
            except Exception:
                pass

    async def save_entry_raw(self, payload: Mapping[str, Any]) -> None:
        """Convenience wrapper: ``raise_config(action='save', value=payload)``."""
        await self.raise_config(action="save", value=payload)

    async def delete_entry_raw(self, payload: Mapping[str, Any]) -> None:
        """Convenience wrapper: ``raise_config(action='delete', value=payload)``.

        Most addons want at least ``{"id": entry_id}`` in the payload.
        """
        await self.raise_config(action="delete", value=payload)
