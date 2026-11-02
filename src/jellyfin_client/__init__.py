"""Jellyfin client with models generated from the OpenAPI spec.

Pick a stack and plug in a transport adapter:

    AsyncJellyfinClient(AiohttpTransport(session), url, info)   # asyncio
    JellyfinClient(HttpxTransport(httpx.Client()), url, info)   # threads
"""

from ._async import (
    AsyncJellyfinClient,
    AsyncJellyfinWebSocket,
    AsyncRemoteControl,
    async_connect_to_address,
)
from ._base import ClientInfo
from ._sync import JellyfinClient, JellyfinWebSocket, RemoteControl, connect_to_address
from .exceptions import (
    JellyfinApiError,
    JellyfinAuthError,
    JellyfinConnectionError,
    JellyfinError,
    JellyfinForbiddenError,
    JellyfinNotFoundError,
)
from .generated import SPEC_VERSION, models
from .transport import AsyncTransport, Request, Response, SyncTransport

__all__ = [
    "SPEC_VERSION",
    "AsyncJellyfinClient",
    "AsyncJellyfinWebSocket",
    "AsyncRemoteControl",
    "AsyncTransport",
    "ClientInfo",
    "JellyfinApiError",
    "JellyfinAuthError",
    "JellyfinClient",
    "JellyfinConnectionError",
    "JellyfinError",
    "JellyfinForbiddenError",
    "JellyfinNotFoundError",
    "JellyfinWebSocket",
    "RemoteControl",
    "Request",
    "Response",
    "SyncTransport",
    "async_connect_to_address",
    "connect_to_address",
    "models",
]
