"""Jellyfin client and server address probing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cached_property
from typing import Any

from .._base import (
    DEFAULT_TIMEOUT,
    ClientBase,
    ClientInfo,
    normalize_address,
    parse_public_info,
    public_info_request,
)
from ..generated import models
from ..generated._async.endpoints import AsyncGeneratedApi
from ..transport import AsyncStreamResponse, AsyncTransport, safe_url
from .remote import AsyncRemoteControl


async def async_connect_to_address(
    transport: AsyncTransport, address: str, *, request_timeout: float = DEFAULT_TIMEOUT
) -> tuple[str, models.PublicSystemInfo]:
    """Resolve an address to the server base URL and its public info.

    Redirects are followed, so the returned URL may differ from the input,
    for example after an http to https redirect.
    """
    url = normalize_address(address)
    response = await transport.send(public_info_request(url), request_timeout)
    return parse_public_info(response, url)


class AsyncJellyfinClient(AsyncGeneratedApi, ClientBase):
    """Client for one Jellyfin server, sending requests through a transport."""

    def __init__(
        self,
        transport: AsyncTransport,
        url: str,
        client_info: ClientInfo,
        *,
        token: str | None = None,
        user_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        ClientBase.__init__(self, url, client_info, token=token, user_id=user_id, timeout=timeout)
        self.transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        request = self._prepare(method, path, params, json, content, content_type)
        response = await self.transport.send(request, self.timeout)
        return self._decode(response, method, path)

    @asynccontextmanager
    async def stream(self, url: str) -> AsyncIterator[AsyncStreamResponse]:
        """Open a binary download, e.g. from a ``*_url()`` builder.

        Iterate ``response.iter_bytes(chunk_size)``; the connection closes when
        the block exits, also when the caller stops reading early.
        """
        async with self.transport.stream(self._download_request(url), self.timeout) as response:
            self._check_status(response.status, "GET", safe_url(url))
            yield response

    async def fetch_bytes(self, url: str) -> bytes:
        """Download a small binary resource such as an image completely."""
        response = await self.transport.send(self._download_request(url), self.timeout)
        self._check_status(response.status, "GET", safe_url(url))
        return response.body

    @cached_property
    def remote(self) -> AsyncRemoteControl:
        """Remote control of client sessions: pause, seek, volume, …"""
        return AsyncRemoteControl(self)

    async def authenticate(self, username: str, password: str) -> models.AuthenticationResult:
        """Log in with username and password and keep the access token."""
        result = await self.authenticate_user_by_name(body={"Username": username, "Pw": password})
        self._store_login(result)
        return result
