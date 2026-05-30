"""App-Token + Session lifecycle.

The dSS exposes a two-stage auth model:

1. **Application Token**: persistent, generated once via
   ``/json/system/requestApplicationToken?applicationName=...`` and approved
   manually in the dSS web UI. Stays valid until revoked.
2. **Session Token**: short-lived, obtained from the app-token via
   ``/json/system/loginApplication?loginToken=<APP_TOKEN>`` and used as the
   ``token=`` query parameter on every subsequent call. Tom refreshes his
   session every 3 minutes in ioBroker - empirical idle-timeout is short.

SessionManager handles the session-token lifecycle transparently with
auto-relogin on 401 / ``not authorized``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import httpx

from .exceptions import DssAuthError, DssConnectionError, DssProtocolError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppToken:
    """Persistent application token (created via dSS web UI).

    Attributes:
        value: The token string (64 hex chars typical).
        application_name: Informational only - the name shown in the dSS
            "Authorized Applications" list. Not sent in any request.
    """

    value: str
    application_name: str = "pydigitalstrom"

    @classmethod
    def from_env(
        cls,
        var_name: str = "DSS_APP_TOKEN",
        application_name: str = "pydigitalstrom",
    ) -> AppToken:
        """Read token from environment variable.

        Args:
            var_name: Env-var name to read.
            application_name: Display name (not transmitted).

        Returns:
            AppToken instance.

        Raises:
            DssAuthError: If the env var is not set or empty.
        """
        value = os.environ.get(var_name, "").strip()
        if not value:
            raise DssAuthError(
                f"[pydss.auth] Environment variable {var_name!r} not set",
                app_token_invalid=True,
            )
        return cls(value=value, application_name=application_name)


class SessionManager:
    """Manages the short-lived session token with auto-relogin.

    Not intended for direct use - DssClient instantiates one internally.
    Provides thread-safe (asyncio) token retrieval via a lock so that
    concurrent requests during a 401 storm trigger only one login.
    """

    def __init__(
        self,
        base_url: str,
        app_token: AppToken,
        http: httpx.AsyncClient,
    ) -> None:
        self._base_url = base_url
        self._app_token = app_token
        self._http = http
        self._session_token: str | None = None
        self._lock = asyncio.Lock()
        self._login_count = 0

    @property
    def login_count(self) -> int:
        """Total number of logins performed (diagnostics)."""
        return self._login_count

    @property
    def current_token(self) -> str | None:
        """Current cached session token, or None if not yet logged in."""
        return self._session_token

    async def get_token(self) -> str:
        """Return a valid session token, logging in if needed."""
        if self._session_token is not None:
            return self._session_token
        return await self._login_locked()

    async def force_refresh(self) -> str:
        """Force a fresh login - called by DssClient on 401.

        Multiple concurrent callers will share a single login round-trip
        thanks to the internal lock.
        """
        return await self._login_locked()

    async def invalidate(self) -> None:
        """Mark the cached token as invalid without immediately re-logging in."""
        async with self._lock:
            self._session_token = None

    async def _login_locked(self) -> str:
        async with self._lock:
            # Another coroutine may have logged in while we waited for the lock
            if self._session_token is not None:
                return self._session_token

            url = f"{self._base_url}/json/system/loginApplication"
            try:
                response = await self._http.get(
                    url,
                    params={"loginToken": self._app_token.value},
                    timeout=10.0,
                )
            except httpx.TimeoutException as exc:
                raise DssAuthError(f"[pydss.auth] Login timed out: {exc}") from exc
            except httpx.RequestError as exc:
                raise DssConnectionError(f"[pydss.auth] Login failed (network): {exc}") from exc

            try:
                data = response.json()
            except ValueError as exc:
                raise DssProtocolError(
                    f"[pydss.auth] Login returned non-JSON (status {response.status_code}): {response.text[:200]}"
                ) from exc

            if not data.get("ok"):
                message = data.get("message", "no message")
                _LOGGER.warning("[pydss.auth] Login rejected: %s", message)
                # The dSS does not reliably distinguish revoked vs malformed tokens.
                # Treat any auth-rejection as app_token_invalid - caller can retry.
                raise DssAuthError(
                    f"[pydss.auth] Login rejected by dSS: {message}",
                    app_token_invalid=True,
                )

            token = data.get("result", {}).get("token")
            if not isinstance(token, str) or not token:
                raise DssProtocolError(
                    f"[pydss.auth] Login response missing token: {data!r}"
                )

            self._session_token = token
            self._login_count += 1
            _LOGGER.info(
                "[pydss.auth] Login OK (session #%d, app=%s)",
                self._login_count,
                self._app_token.application_name,
            )
            return token
