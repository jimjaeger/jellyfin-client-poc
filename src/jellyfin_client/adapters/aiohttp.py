"""Async transport based on aiohttp."""

from __future__ import annotations

import asyncio

import aiohttp

from ..exceptions import JellyfinConnectionError
from ..transport import Request, Response, websocket_rejected

# Detects silently dropped WebSocket connections; the server answers pings.
DEFAULT_HEARTBEAT = 30.0


class AiohttpTransport:
    """Transport using an aiohttp session owned by the caller."""

    def __init__(
        self, session: aiohttp.ClientSession, *, heartbeat: float = DEFAULT_HEARTBEAT
    ) -> None:
        self._session = session
        self._heartbeat = heartbeat

    async def send(self, request: Request, timeout: float) -> Response:
        try:
            async with self._session.request(
                request.method,
                request.url,
                params=request.params or None,
                headers=request.headers,
                json=request.json,
                data=request.content,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                return Response(resp.status, await resp.read(), str(resp.url))
        except (aiohttp.ClientError, TimeoutError) as err:
            raise JellyfinConnectionError(f"{request.method} {request.url} failed: {err}") from err

    async def connect_websocket(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> AiohttpWebSocket:
        try:
            async with asyncio.timeout(timeout):
                ws = await self._session.ws_connect(url, headers=headers, heartbeat=self._heartbeat)
        except aiohttp.WSServerHandshakeError as err:
            raise websocket_rejected(err.status) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise JellyfinConnectionError(f"WebSocket connection failed: {err}") from err
        return AiohttpWebSocket(ws)


class AiohttpWebSocket:
    """Open aiohttp WebSocket connection."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws

    async def send_text(self, data: str) -> None:
        try:
            await self._ws.send_str(data)
        except (ConnectionResetError, aiohttp.ClientError) as err:
            raise JellyfinConnectionError(f"WebSocket send failed: {err}") from err

    async def receive_text(self, timeout: float) -> str | None:
        try:
            msg = await self._ws.receive(timeout=timeout)
        except TimeoutError:
            return None
        if msg.type is aiohttp.WSMsgType.TEXT:
            return str(msg.data)
        if msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSING,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            raise JellyfinConnectionError(f"WebSocket closed with code {self._ws.close_code}")
        return None

    async def close(self) -> None:
        await self._ws.close()
