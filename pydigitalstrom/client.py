"""DssClient - async HTTP wrapper with auto-relogin on 401."""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any

import httpx

from .auth import AppToken, SessionManager
from .exceptions import (
    DssAuthError,
    DssConnectionError,
    DssProtocolError,
    DssTimeoutError,
)

_LOGGER = logging.getLogger(__name__)


class DssClient:
    """Async HTTP client for the dSS REST API.

    Usage::

        token = AppToken.from_env("DSS_APP_TOKEN")
        async with DssClient("<dss-hostname-or-ip>", token) as client:
            apartment = await fetch_apartment(client)

    The client manages a single :class:`SessionManager` that handles
    login + 401 retries transparently. Most methods unwrap the dSS
    ``{"result": ..., "ok": true}`` envelope and return the ``result``
    field directly.
    """

    def __init__(
        self,
        host: str,
        app_token: AppToken,
        *,
        port: int = 8080,
        verify_ssl: bool = False,
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Args:
            host: dSS hostname or IP.
            app_token: Persistent application token.
            port: HTTPS port (dSS default 8080).
            verify_ssl: False for self-signed certs (dSS default). Set True
                only if you have pinned the cert via custom http_client.
            timeout: Default per-request HTTP timeout in seconds.
            http_client: Optional pre-configured httpx.AsyncClient (e.g. for
                test mocking). If None, a default client is created.
        """
        self._host = host
        self._port = port
        self._app_token = app_token
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(verify=verify_ssl, timeout=timeout)
        self._session = SessionManager(self.base_url, app_token, self._http)

    @property
    def base_url(self) -> str:
        return f"https://{self._host}:{self._port}"

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def app_token(self) -> AppToken:
        return self._app_token

    @property
    def session(self) -> SessionManager:
        """Underlying session manager - exposed for diagnostics."""
        return self._session

    async def __aenter__(self) -> DssClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def login(self) -> None:
        """Perform an explicit login. Optional - lazy login happens on first call."""
        await self._session.get_token()

    async def close(self) -> None:
        """Close the underlying HTTP client (if owned) and invalidate session."""
        await self._session.invalidate()
        if self._owns_http:
            await self._http.aclose()

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        unwrap_result: bool = True,
    ) -> Any:
        """GET a dSS JSON endpoint with auto-relogin on auth failure.

        Args:
            path: API path with leading slash, e.g. ``/json/apartment/getStructure``.
            params: Query parameters. ``token`` is set automatically.
            timeout: Per-request timeout override.
            unwrap_result: If True (default), returns ``data["result"]`` on success.
                If False, returns the full ``{"result": ..., "ok": ...}`` dict.

        Returns:
            The ``result`` field if unwrap_result is True, else the full response dict.

        Raises:
            DssTimeoutError: HTTP timeout (after retry).
            DssConnectionError: Network error.
            DssAuthError: Re-login failed.
            DssProtocolError: Unexpected response shape or ok=false (non-auth).
        """
        return await self._request(
            path,
            params,
            timeout=timeout,
            unwrap_result=unwrap_result,
            allow_retry=True,
        )

    async def get_raw(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Like :meth:`get` but returns the raw httpx Response.

        Useful when the caller wants to inspect headers or stream the response
        body. Does NOT retry on 401 - caller is responsible.
        """
        merged = dict(params or {})
        merged["token"] = await self._session.get_token()
        try:
            return await self._http.get(
                f"{self.base_url}{path}",
                params=merged,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise DssTimeoutError(f"[pydss.client] Timeout on {path}: {exc}") from exc
        except httpx.RequestError as exc:
            raise DssConnectionError(f"[pydss.client] Network error on {path}: {exc}") from exc

    async def event_long_poll(
        self,
        subscription_id: int,
        timeout_ms: int = 30000,
    ) -> list[dict[str, Any]]:
        """Long-poll ``/json/event/get``.

        Returns events accumulated since the last call (empty list on timeout).
        Uses an HTTP timeout of ``timeout_ms / 1000 + 10s`` to allow the
        server-side timeout to fire first.

        Args:
            subscription_id: The numeric subscription id used at subscribe time.
            timeout_ms: Server-side long-poll timeout in milliseconds.

        Returns:
            List of raw event dicts. Each event has at minimum a ``name`` and
            ``properties`` field; ``source`` is typically also present.
        """
        http_timeout = (timeout_ms / 1000.0) + 10.0
        data = await self._request(
            "/json/event/get",
            params={"subscriptionID": subscription_id, "timeout": timeout_ms},
            timeout=http_timeout,
            unwrap_result=True,
            allow_retry=True,
        )
        if isinstance(data, dict):
            events = data.get("events", [])
            if isinstance(events, list):
                return events
        return []

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None,
        *,
        timeout: float | None,
        unwrap_result: bool,
        allow_retry: bool,
    ) -> Any:
        merged = dict(params or {})
        merged["token"] = await self._session.get_token()

        try:
            response = await self._http.get(
                f"{self.base_url}{path}",
                params=merged,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise DssTimeoutError(f"[pydss.client] Timeout on {path}: {exc}") from exc
        except httpx.RequestError as exc:
            raise DssConnectionError(f"[pydss.client] Network error on {path}: {exc}") from exc

        # HTTP-level auth failures
        if response.status_code in (401, 403):
            if allow_retry:
                _LOGGER.info("[pydss.client] HTTP %d on %s - re-login", response.status_code, path)
                await self._session.force_refresh()
                return await self._request(
                    path,
                    params,
                    timeout=timeout,
                    unwrap_result=unwrap_result,
                    allow_retry=False,
                )
            raise DssAuthError(
                f"[pydss.client] HTTP {response.status_code} on {path} after re-login"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise DssProtocolError(
                f"[pydss.client] Non-JSON response on {path} (status {response.status_code}): "
                f"{response.text[:200]}"
            ) from exc

        if not isinstance(data, dict):
            raise DssProtocolError(f"[pydss.client] Response not a JSON object on {path}: {data!r}")

        if not data.get("ok"):
            message = (data.get("message") or "").lower()
            # Soft auth errors - dSS sometimes returns 200 + ok=false instead of 401
            if allow_retry and ("not authorized" in message or "authentication" in message):
                _LOGGER.info("[pydss.client] Soft auth failure on %s: %s - re-login", path, message)
                await self._session.force_refresh()
                return await self._request(
                    path,
                    params,
                    timeout=timeout,
                    unwrap_result=unwrap_result,
                    allow_retry=False,
                )
            raise DssProtocolError(
                f"[pydss.client] dSS returned ok=false on {path}: {data.get('message', 'no message')!r}"
            )

        if unwrap_result:
            return data.get("result")
        return data
