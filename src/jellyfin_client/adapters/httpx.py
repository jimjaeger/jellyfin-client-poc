"""Sync and async transports based on httpx, with websockets for the WebSocket.

``httpx.Client(http2=True)`` enables HTTP/2 when the ``h2`` package is installed.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import ssl
from typing import Any

import httpx

# httpcore loads the anyio asyncio backend on the first async request; import
# it here so that does not happen inside the event loop (Home Assistant's
# bootstrap does the same).
from anyio._backends import _asyncio  # noqa: F401
from websockets.asyncio.client import ClientConnection as AsyncClientConnection
from websockets.asyncio.client import connect as async_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import ClientConnection as SyncClientConnection
from websockets.sync.client import connect as sync_connect

from ..exceptions import JellyfinConnectionError
from ..transport import Request, Response, websocket_rejected

# Detects silently dropped WebSocket connections; the server answers pings.
DEFAULT_PING_INTERVAL = 30.0

# websockets 17 deprecates calling the sync connect() directly unless the
# caller opts in with legacy=True; older versions lack the flag.
_SYNC_CONNECT_KWARGS: dict[str, Any] = (
    {"legacy": True} if "legacy" in inspect.signature(sync_connect).parameters else {}
)


_TOKEN_RE = re.compile(r'Token="[^"]*"')


class _RedactTokenFilter(logging.Filter):
    """Mask access tokens in the request headers websockets logs at debug level."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                _TOKEN_RE.sub('Token="***"', arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


_WS_LOGGER = logging.getLogger(f"{__name__}.websockets")
_WS_LOGGER.addFilter(_RedactTokenFilter())


def _ssl_for(url: str, context: ssl.SSLContext | None) -> ssl.SSLContext | None:
    return context if url.startswith("wss") else None


class HttpxTransport:
    """Sync transport using an httpx client owned by the caller.

    Pass ``ssl_context`` for client certificates on the WebSocket; configure
    the same certificates on the httpx client for REST.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        ping_interval: float = DEFAULT_PING_INTERVAL,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._client = client
        self._ping_interval = ping_interval
        self._ssl_context = ssl_context

    def send(self, request: Request, timeout: float) -> Response:
        try:
            resp = self._client.request(
                request.method,
                request.url,
                params=request.params,
                headers=request.headers,
                json=request.json,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as err:
            raise JellyfinConnectionError(f"{request.method} {request.url} failed: {err}") from err
        return Response(resp.status_code, resp.content, str(resp.url))

    def connect_websocket(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> HttpxSyncWebSocket:
        try:
            ws = sync_connect(
                url,
                additional_headers=headers,
                open_timeout=timeout,
                ping_interval=self._ping_interval,
                ssl=_ssl_for(url, self._ssl_context),
                logger=_WS_LOGGER,
                **_SYNC_CONNECT_KWARGS,
            )
        except InvalidStatus as err:
            raise websocket_rejected(err.response.status_code) from err
        except (OSError, TimeoutError, WebSocketException) as err:
            raise JellyfinConnectionError(f"WebSocket connection failed: {err}") from err
        return HttpxSyncWebSocket(ws)


class HttpxSyncWebSocket:
    """Open sync WebSocket connection; close() may be called from any thread."""

    def __init__(self, ws: SyncClientConnection) -> None:
        self._ws = ws

    def send_text(self, data: str) -> None:
        try:
            self._ws.send(data)
        except ConnectionClosed as err:
            raise JellyfinConnectionError(f"WebSocket send failed: {err}") from err

    def receive_text(self, timeout: float) -> str | None:
        try:
            data = self._ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        except ConnectionClosed as err:
            raise JellyfinConnectionError(f"WebSocket closed: {err}") from err
        return data if isinstance(data, str) else None

    def close(self) -> None:
        self._ws.close()


class HttpxAsyncTransport:
    """Async transport using an httpx async client owned by the caller."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        ping_interval: float = DEFAULT_PING_INTERVAL,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._client = client
        self._ping_interval = ping_interval
        self._ssl_context = ssl_context

    async def send(self, request: Request, timeout: float) -> Response:
        try:
            resp = await self._client.request(
                request.method,
                request.url,
                params=request.params,
                headers=request.headers,
                json=request.json,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as err:
            raise JellyfinConnectionError(f"{request.method} {request.url} failed: {err}") from err
        return Response(resp.status_code, resp.content, str(resp.url))

    async def connect_websocket(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> HttpxAsyncWebSocket:
        try:
            ws = await async_connect(
                url,
                additional_headers=headers,
                open_timeout=timeout,
                ping_interval=self._ping_interval,
                ssl=_ssl_for(url, self._ssl_context),
                logger=_WS_LOGGER,
            )
        except InvalidStatus as err:
            raise websocket_rejected(err.response.status_code) from err
        except (OSError, TimeoutError, WebSocketException) as err:
            raise JellyfinConnectionError(f"WebSocket connection failed: {err}") from err
        return HttpxAsyncWebSocket(ws)


class HttpxAsyncWebSocket:
    """Open async WebSocket connection."""

    def __init__(self, ws: AsyncClientConnection) -> None:
        self._ws = ws

    async def send_text(self, data: str) -> None:
        try:
            await self._ws.send(data)
        except ConnectionClosed as err:
            raise JellyfinConnectionError(f"WebSocket send failed: {err}") from err

    async def receive_text(self, timeout: float) -> str | None:
        try:
            async with asyncio.timeout(timeout):
                data = await self._ws.recv()
        except TimeoutError:
            return None
        except ConnectionClosed as err:
            raise JellyfinConnectionError(f"WebSocket closed: {err}") from err
        return data if isinstance(data, str) else None

    async def close(self) -> None:
        await self._ws.close()
