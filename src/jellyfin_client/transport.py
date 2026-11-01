"""Transport protocols implemented by the HTTP adapters.

The client core builds requests and interprets responses; adapters only move
bytes. Each stack has its own protocol, so a sync adapter plugs into the sync
client and an async adapter into the async client.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .exceptions import (
    JellyfinAuthError,
    JellyfinConnectionError,
    JellyfinError,
    JellyfinForbiddenError,
)


@dataclass(frozen=True, slots=True)
class Request:
    """An HTTP request to send; adapters follow redirects."""

    method: str
    url: str
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    json: Any = None


@dataclass(frozen=True, slots=True)
class Response:
    """The raw response; ``url`` is the final URL after redirects."""

    status: int
    body: bytes
    url: str


class AsyncWebSocket(Protocol):
    """An open WebSocket connection."""

    async def send_text(self, data: str) -> None:
        """Send a text frame."""

    async def receive_text(self, timeout: float) -> str | None:
        """Return the next text frame, or None if none arrived in time.

        Raises JellyfinConnectionError once the connection is closed.
        """

    async def close(self) -> None:
        """Close the connection; safe to call from any task."""


class SyncWebSocket(Protocol):
    """An open WebSocket connection."""

    def send_text(self, data: str) -> None:
        """Send a text frame."""

    def receive_text(self, timeout: float) -> str | None:
        """Return the next text frame, or None if none arrived in time.

        Raises JellyfinConnectionError once the connection is closed.
        """

    def close(self) -> None:
        """Close the connection; safe to call from any thread."""


class AsyncTransport(Protocol):
    """Async HTTP and WebSocket transport."""

    async def send(self, request: Request, timeout: float) -> Response:
        """Send a request; raise JellyfinConnectionError if it cannot be sent."""

    async def connect_websocket(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> AsyncWebSocket:
        """Open a WebSocket; raise websocket_rejected() on a refused handshake."""


class SyncTransport(Protocol):
    """Sync HTTP and WebSocket transport."""

    def send(self, request: Request, timeout: float) -> Response:
        """Send a request; raise JellyfinConnectionError if it cannot be sent."""

    def connect_websocket(self, url: str, headers: dict[str, str], timeout: float) -> SyncWebSocket:
        """Open a WebSocket; raise websocket_rejected() on a refused handshake."""


type AsyncConnectCallback = Callable[[], Awaitable[None]]
type SyncConnectCallback = Callable[[], None]


def websocket_rejected(status: int) -> JellyfinError:
    """Map a refused WebSocket handshake to the matching error."""
    if status == 401:
        return JellyfinAuthError("WebSocket connection was rejected")
    if status == 403:
        return JellyfinForbiddenError("WebSocket connection is not permitted for this user")
    return JellyfinConnectionError(f"WebSocket handshake failed with HTTP {status}")
