"""Generic property-tree walker with type awareness.

The dSS property tree is a hierarchical key-value store accessible via
``/json/property/getChildren``, ``getString``, ``getInteger``, ``getBoolean``.
Calling the wrong getter (e.g. getString on an int field) returns a
``Property-Type mismatch`` error - this module handles that by inspecting
the child's declared type first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .client import DssClient

_LOGGER = logging.getLogger(__name__)


class PropertyType(str, Enum):
    """Property type as reported by ``getChildren``."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    GROUP = "none"  # "none" = subtree, has no leaf value


@dataclass(frozen=True, slots=True)
class PropertyChild:
    """Single entry in a ``getChildren`` response."""

    name: str
    type: PropertyType

    @property
    def is_leaf(self) -> bool:
        return self.type is not PropertyType.GROUP


class PropertyTreeWalker:
    """Walker for the dSS property tree.

    Usage::

        walker = PropertyTreeWalker(client)
        children = await walker.get_children("/scripts/system-addon-timed-events/entries")
        value = await walker.get_typed("/scripts/.../offset", PropertyType.INTEGER)
        tree = await walker.walk("/usr/triggers")  # recursive dump
    """

    def __init__(self, client: DssClient, *, max_depth: int = 8) -> None:
        self._client = client
        self._max_depth = max_depth

    async def get_children(self, path: str) -> list[PropertyChild]:
        """List direct children of ``path`` with their types."""
        raw = await self._client.get(
            "/json/property/getChildren",
            params={"path": path},
        )
        if not isinstance(raw, list):
            return []
        out: list[PropertyChild] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            type_raw = entry.get("type", "none")
            if not name:
                continue
            try:
                ptype = PropertyType(type_raw)
            except ValueError:
                ptype = PropertyType.GROUP
            out.append(PropertyChild(name=str(name), type=ptype))
        return out

    async def get_typed(
        self,
        path: str,
        prop_type: PropertyType | None = None,
    ) -> str | int | bool | None:
        """Read a single leaf value with the right getter.

        If ``prop_type`` is None, the parent's children are listed first to
        discover the type. Pass the type explicitly when known to save a round-trip.
        """
        if prop_type is None:
            prop_type = await self._discover_type(path)
            if prop_type is None or prop_type is PropertyType.GROUP:
                return None

        endpoint = {
            PropertyType.STRING: "/json/property/getString",
            PropertyType.INTEGER: "/json/property/getInteger",
            PropertyType.BOOLEAN: "/json/property/getBoolean",
        }.get(prop_type)
        if endpoint is None:
            return None

        result = await self._client.get(endpoint, params={"path": path})
        if isinstance(result, dict):
            return result.get("value")  # type: ignore[no-any-return]
        return None

    async def set_typed(self, path: str, value: str | int | bool) -> None:
        """Write a leaf value with the right setter inferred from ``value``'s Python type."""
        if isinstance(value, bool):
            endpoint = "/json/property/setBoolean"
            v: str = "true" if value else "false"
        elif isinstance(value, int):
            endpoint = "/json/property/setInteger"
            v = str(value)
        elif isinstance(value, str):
            endpoint = "/json/property/setString"
            v = value
        else:
            raise TypeError(f"[pydss.property] Unsupported value type: {type(value)}")

        await self._client.get(endpoint, params={"path": path, "value": v})

    async def walk(
        self,
        root: str,
        *,
        max_depth: int | None = None,
        leaf_filter: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        """Recursively dump a subtree as a nested dict.

        Leaves become their typed values (str/int/bool). Subtrees become
        nested dicts. Empty subtrees become ``{}``.

        Args:
            root: Starting path.
            max_depth: Override the walker's default depth limit.
            leaf_filter: Optional predicate on leaf path - return False to skip
                reading the value (useful for huge property trees where you
                only care about certain fields).
        """
        depth = max_depth if max_depth is not None else self._max_depth
        return await self._walk_impl(root, depth, leaf_filter)

    async def _walk_impl(
        self,
        path: str,
        remaining_depth: int,
        leaf_filter: Callable[[str], bool] | None,
    ) -> dict[str, Any]:
        if remaining_depth < 0:
            return {"_depth_exceeded": True}

        children = await self.get_children(path)
        if not children:
            return {}

        node: dict[str, Any] = {}
        for c in children:
            child_path = _join_path(path, c.name)
            if c.is_leaf:
                if leaf_filter is not None and not leaf_filter(child_path):
                    continue
                try:
                    node[c.name] = await self.get_typed(child_path, c.type)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("[pydss.property] Leaf %s failed: %s", child_path, exc)
                    node[c.name] = None
            else:
                node[c.name] = await self._walk_impl(child_path, remaining_depth - 1, leaf_filter)
        return node

    async def _discover_type(self, path: str) -> PropertyType | None:
        """Find the type of ``path`` by listing its parent's children."""
        parent, _, leaf_name = path.rpartition("/")
        if not parent or not leaf_name:
            return None
        siblings = await self.get_children(parent)
        for s in siblings:
            if s.name == leaf_name:
                return s.type
        return None


def _join_path(base: str, name: str) -> str:
    if base.endswith("/"):
        return f"{base}{name}"
    return f"{base}/{name}"
