"""Constants for the digitalSTROM integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "digitalstrom"

# Config entry data keys
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_APP_TOKEN: Final = "app_token"
CONF_VERIFY_SSL: Final = "verify_ssl"

# Sane defaults
DEFAULT_PORT: Final = 8080
DEFAULT_VERIFY_SSL: Final = False  # dSS ships with self-signed certs

# HA bus events
EVENT_CALL_SCENE: Final = "digitalstrom_call_scene"
EVENT_BUTTON_CLICK: Final = "digitalstrom_button_click"
EVENT_STATE_CHANGE: Final = "digitalstrom_state_change"

# Service names
SERVICE_CALL_SCENE: Final = "call_scene"
SERVICE_UNDO_SCENE: Final = "undo_scene"

# Device manufacturer
MANUFACTURER: Final = "digitalSTROM"
