"""WebSocket for server-side events.

The connection is driven by a single receive loop without helper tasks or
threads, so the same code works for both stacks.
"""

from __future__ import annotations

import json
import logging
import random
import time
from asyncio import sleep
from collections.abc import Callable
from typing import Any

from ..exceptions import JellyfinConnectionError
from ..generated import models
from ..transport import AsyncConnectCallback, AsyncWebSocket
from .client import AsyncJellyfinClient

_LOGGER = logging.getLogger(__name__)

# Initial delay and interval in ms for session updates, as jellyfin-web uses.
SESSIONS_TIMING = "0,1500"
# Receive timeout until the server has told us its keep-alive timeout.
IDLE_RECEIVE_TIMEOUT = 30.0
# Granularity of the reconnect wait, so stop() takes effect quickly.
STOP_POLL_INTERVAL = 0.2
KEEPALIVE = json.dumps({"MessageType": "KeepAlive"})

type MessageCallback = Callable[[models.OutboundWebSocketMessage], None]


class AsyncJellyfinWebSocket:
    """Keep a WebSocket to the server open and dispatch its messages.

    Messages are change hints, not full state: the server does not push the
    current sessions on subscribe. ``on_connect`` runs after every
    (re)connect so callers can load or resync state over REST.
    """

    def __init__(
        self,
        client: AsyncJellyfinClient,
        on_message: MessageCallback,
        *,
        on_connect: AsyncConnectCallback | None = None,
        subscribe_sessions: bool = True,
        min_backoff: float = 1.0,
        max_backoff: float = 300.0,
    ) -> None:
        self._client = client
        self._on_message = on_message
        self._on_connect = on_connect
        self._subscribe_sessions = subscribe_sessions
        self._min_backoff = min_backoff
        self._max_backoff = max_backoff
        self._connection: AsyncWebSocket | None = None
        self._stopped = False
        self.connected = False

    async def run(self) -> None:
        """Connect and reconnect until stop() is called.

        Raises JellyfinAuthError instead of retrying, since a rejected token
        will not become valid by waiting.
        """
        delay = self._min_backoff
        while not self._stopped:
            try:
                await self._connect_and_listen()
            except JellyfinConnectionError as err:
                _LOGGER.debug("WebSocket connection failed: %s", err)
            if self.connected:
                delay = self._min_backoff
            self.connected = False
            await self._wait(delay * random.uniform(0.5, 1.5))
            delay = min(delay * 2, self._max_backoff)

    async def stop(self) -> None:
        """Close the connection and end run()."""
        self._stopped = True
        if self._connection is not None:
            await self._connection.close()

    async def _wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self._stopped and time.monotonic() < deadline:
            await sleep(min(STOP_POLL_INTERVAL, deadline - time.monotonic()))

    async def _connect_and_listen(self) -> None:
        connection = await self._client.transport.connect_websocket(
            self._client.websocket_url,
            {"Authorization": self._client.authorization},
            self._client.timeout,
        )
        self._connection = connection
        self.connected = True
        try:
            if self._stopped:
                return
            if self._subscribe_sessions:
                await connection.send_text(
                    json.dumps({"MessageType": "SessionsStart", "Data": SESSIONS_TIMING})
                )
            if self._on_connect:
                await self._on_connect()
            await self._listen(connection)
        finally:
            self._connection = None
            await connection.close()

    async def _listen(self, connection: AsyncWebSocket) -> None:
        keepalive_interval: float | None = None
        last_keepalive = time.monotonic()
        while not self._stopped:
            timeout = IDLE_RECEIVE_TIMEOUT
            if keepalive_interval is not None:
                due_in = keepalive_interval - (time.monotonic() - last_keepalive)
                if due_in <= 0:
                    # The server drops the connection unless it hears from us in time.
                    await connection.send_text(KEEPALIVE)
                    last_keepalive = time.monotonic()
                    due_in = keepalive_interval
                timeout = due_in
            text = await connection.receive_text(timeout)
            if text is None:
                continue
            message = _parse(text)
            if message is None:
                continue
            if message.get("MessageType") == "ForceKeepAlive":
                # The data is the server timeout; answer at least every half of it.
                keepalive_interval = max(float(message.get("Data") or 60) / 2, 1.0)
                last_keepalive = time.monotonic() - keepalive_interval
            self._dispatch(message)

    def _dispatch(self, message: Any) -> None:
        try:
            self._on_message(message)
        except Exception:
            _LOGGER.exception("Error handling WebSocket message %s", message.get("MessageType"))


def _parse(data: str) -> dict[str, Any] | None:
    try:
        message = json.loads(data)
    except ValueError:
        _LOGGER.debug("Ignoring malformed WebSocket message")
        return None
    if not isinstance(message, dict) or "MessageType" not in message:
        _LOGGER.debug("Ignoring WebSocket message without MessageType")
        return None
    return message
