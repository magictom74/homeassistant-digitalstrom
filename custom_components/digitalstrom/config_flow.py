"""Config flow for digitalSTROM."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.httpx_client import get_async_client

from pydigitalstrom import (
    AppToken,
    DssClient,
    fetch_apartment_name,
)

from .const import (
    CONF_APP_TOKEN,
    CONF_HOST,
    CONF_PORT,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_APP_TOKEN): cv.string,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
    }
)


class DigitalStromConfigFlow(ConfigFlow, domain=DOMAIN):
    """One config entry per dSS Brain.

    Prerequisite: the user has created an application token via the dSS
    web UI (under System -> Access -> Authorized Applications) and
    approved it. The token value is the long string the dSS displays.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            token = user_input[CONF_APP_TOKEN]
            verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

            name = await self._probe(host, port, token, verify_ssl, errors)
            if name is not None:
                # unique_id = host:port - one entry per dSS
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: host, CONF_PORT: port}
                )
                return self.async_create_entry(
                    title=name or f"digitalSTROM ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_APP_TOKEN: token,
                        CONF_VERIFY_SSL: verify_ssl,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def _probe(
        self,
        host: str,
        port: int,
        token: str,
        verify_ssl: bool,
        errors: dict[str, str],
    ) -> str | None:
        """Test login + fetch apartment name."""
        client = DssClient(
            host,
            AppToken(value=token, application_name="HomeAssistant"),
            port=port,
            verify_ssl=verify_ssl,
            http_client=get_async_client(self.hass, verify_ssl=verify_ssl),
        )
        try:
            await client.login()
            return await fetch_apartment_name(client)
        except Exception as exc:
            _LOGGER.warning("[digitalstrom.config_flow] probe failed: %s", exc)
            msg = str(exc).lower()
            if "401" in msg or "unauthorized" in msg or "token" in msg:
                errors["base"] = "invalid_auth"
            elif "timeout" in msg or "connect" in msg:
                errors["base"] = "cannot_connect"
            else:
                errors["base"] = "unknown"
            return None
        finally:
            with contextlib.suppress(Exception):
                await client.close()
