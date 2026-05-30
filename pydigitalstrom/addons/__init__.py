"""System-addon wrappers for the dSS.

Each addon module exposes a small typed CRUD API on top of the universal
property-tree + ``event/raise`` pattern factored into :class:`AddonBase`.
"""

from __future__ import annotations

from .base import AddonBase
from .scene_responder import (
    SceneResponderAddon,
    parse_scene_responder,
    serialize_scene_responder_for_save,
)
from .timed_events import TimedEventsAddon, parse_timed_event, serialize_timed_event
from .user_actions import (
    UserActionsAddon,
    parse_user_action,
    serialize_user_action,
    serialize_user_action_for_save,
)
from .user_states import (
    UserStatesAddon,
    parse_user_state,
    serialize_user_state_for_save,
)

__all__ = [
    "AddonBase",
    "SceneResponderAddon",
    "TimedEventsAddon",
    "UserActionsAddon",
    "UserStatesAddon",
    "parse_scene_responder",
    "parse_timed_event",
    "parse_user_action",
    "parse_user_state",
    "serialize_scene_responder_for_save",
    "serialize_timed_event",
    "serialize_user_action",
    "serialize_user_action_for_save",
    "serialize_user_state_for_save",
]
