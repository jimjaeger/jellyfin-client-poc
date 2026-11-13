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
from .discovery import DiscoveredServer, async_discover_servers, discover_servers
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
    "DiscoveredServer",
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
    "async_discover_servers",
    "connect_to_address",
    "discover_servers",
    "models",
]
