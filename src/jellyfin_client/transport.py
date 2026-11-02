"""Transport protocols implemented by the HTTP adapters.

The client core builds requests and interprets responses; adapters only move
bytes. Each stack has its own protocol, so a sync adapter plugs into the sync
client and an async adapter into the async client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager
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
    """An HTTP request to send; adapters follow redirects.

    The body is either ``json`` or raw ``content`` (with its Content-Type in
    ``headers``), never both.
    """

    method: str
    url: str
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    json: Any = None
    content: bytes | None = None


@dataclass(frozen=True, slots=True)
class Response:
    """The raw response; ``url`` is the final URL after redirects."""

    status: int
    body: bytes
    url: str


class AsyncStreamResponse(Protocol):
    """A response whose body is read in chunks."""

    @property
    def status(self) -> int:
        """HTTP status code."""

    @property
    def url(self) -> str:
        """Final URL after redirects."""

    def iter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        """Yield the body in chunks; raise JellyfinConnectionError on failure."""


class SyncStreamResponse(Protocol):
    """A response whose body is read in chunks."""

    @property
    def status(self) -> int:
        """HTTP status code."""

    @property
    def url(self) -> str:
        """Final URL after redirects."""

    def iter_bytes(self, chunk_size: int) -> Iterator[bytes]:
        """Yield the body in chunks; raise JellyfinConnectionError on failure."""


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

    def stream(
        self, request: Request, timeout: float
    ) -> AbstractAsyncContextManager[AsyncStreamResponse]:
        """Open a streaming response, closed when the context exits.

        ``timeout`` limits each read, not the whole download.
        """


class SyncTransport(Protocol):
    """Sync HTTP and WebSocket transport."""

    def send(self, request: Request, timeout: float) -> Response:
        """Send a request; raise JellyfinConnectionError if it cannot be sent."""

    def connect_websocket(self, url: str, headers: dict[str, str], timeout: float) -> SyncWebSocket:
        """Open a WebSocket; raise websocket_rejected() on a refused handshake."""

    def stream(
        self, request: Request, timeout: float
    ) -> AbstractContextManager[SyncStreamResponse]:
        """Open a streaming response, closed when the context exits.

        ``timeout`` limits each read, not the whole download.
        """


type AsyncConnectCallback = Callable[[], Awaitable[None]]
type SyncConnectCallback = Callable[[], None]


def websocket_rejected(status: int) -> JellyfinError:
    """Map a refused WebSocket handshake to the matching error."""
    if status == 401:
        return JellyfinAuthError("WebSocket connection was rejected")
    if status == 403:
        return JellyfinForbiddenError("WebSocket connection is not permitted for this user")
    return JellyfinConnectionError(f"WebSocket handshake failed with HTTP {status}")


def safe_url(url: str) -> str:
    """URL without its query, for error messages; the query may carry a token."""
    return url.split("?", 1)[0]
