"""Fake Jellyfin server for tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from jellyfin_client import (
    AsyncJellyfinClient,
    AsyncJellyfinWebSocket,
    ClientInfo,
    JellyfinClient,
    JellyfinWebSocket,
    async_connect_to_address,
    connect_to_address,
)
from jellyfin_client.adapters.aiohttp import AiohttpTransport
from jellyfin_client.adapters.httpx import HttpxAsyncTransport, HttpxTransport

TOKEN = "secret-token"
USER_ID = "user-1"
CLIENT_INFO = ClientInfo(
    client="Home Assistant", version="1.0", device='Living room, "TV"', device_id="dev-1"
)

DOWNLOAD_CHUNK = bytes(range(256)) * 64
DOWNLOAD_CHUNKS = 32

PUBLIC_INFO = {"Id": "server-1", "ServerName": "Test", "Version": "12.4.0"}
SESSIONS = [
    {
        "Id": "session-1",
        "UserName": "alice",
        # Values a newer server might send: unknown enum value and unknown field.
        "PlayState": {"PlayMethod": "SomethingNew", "IsPaused": False},
        "FieldFromTheFuture": 1,
    }
]


@dataclass
class FakeServer:
    """Records requests and lets tests change answers."""

    requests: list[dict[str, Any]] = field(default_factory=list)
    ws_messages: list[dict[str, Any]] = field(default_factory=list)
    ws_connections: int = 0
    reject_ws: bool = False
    close_ws_once: bool = False
    ws_script: list[dict[str, Any]] = field(default_factory=list)
    server: TestServer | None = None

    @property
    def url(self) -> str:
        assert self.server
        return str(self.server.make_url("")).rstrip("/")


def _authorized(request: web.Request) -> bool:
    return f'Token="{TOKEN}"' in request.headers.get("Authorization", "")


def build_app(fake: FakeServer) -> web.Application:
    async def record(request: web.Request) -> None:
        raw = await request.read() if request.can_read_body else b""
        body = await request.json() if raw and request.content_type == "application/json" else None
        fake.requests.append(
            {
                "method": request.method,
                "path": request.path,
                "raw_path": request.raw_path.split("?")[0],
                "query": dict(request.query),
                "headers": dict(request.headers),
                "json": body,
                "raw": raw,
                "content_type": request.content_type,
            }
        )

    async def public_info(request: web.Request) -> web.Response:
        await record(request)
        return web.json_response(PUBLIC_INFO)

    async def redirect(request: web.Request) -> web.Response:
        raise web.HTTPFound("/jellyfin/System/Info/Public")

    async def authenticate(request: web.Request) -> web.Response:
        await record(request)
        body = await request.json()
        if body != {"Username": "alice", "Pw": "pw"}:
            return web.Response(status=401)
        return web.json_response({"AccessToken": TOKEN, "User": {"Id": USER_ID, "Name": "alice"}})

    async def sessions(request: web.Request) -> web.Response:
        await record(request)
        if not _authorized(request):
            return web.Response(status=401)
        return web.json_response(SESSIONS)

    async def no_content(request: web.Request) -> web.Response:
        await record(request)
        if not _authorized(request):
            return web.Response(status=401)
        return web.Response(status=204)

    async def lyrics(request: web.Request) -> web.Response:
        await record(request)
        return web.json_response({"Lyrics": [{"Text": (await request.text()).strip()}]})

    async def download(request: web.Request) -> web.StreamResponse:
        await record(request)
        if not _authorized(request):
            return web.Response(status=401)
        response = web.StreamResponse()
        await response.prepare(request)
        for _ in range(DOWNLOAD_CHUNKS):
            await response.write(DOWNLOAD_CHUNK)
        await response.write_eof()
        return response

    async def admin_only(request: web.Request) -> web.Response:
        await record(request)
        return web.Response(status=403 if _authorized(request) else 401)

    async def socket(request: web.Request) -> web.WebSocketResponse:
        fake.ws_connections += 1
        fake.requests.append({"path": request.path, "headers": dict(request.headers)})
        if fake.reject_ws or not _authorized(request):
            raise web.HTTPUnauthorized
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        for message in fake.ws_script:
            await ws.send_json(message)
        if fake.close_ws_once:
            fake.close_ws_once = False
            await ws.close()
            return ws
        async for msg in ws:
            if msg.type is aiohttp.WSMsgType.TEXT:
                fake.ws_messages.append(msg.json())
        return ws

    app = web.Application()
    app.router.add_get("/System/Info/Public", public_info)
    app.router.add_get("/old/System/Info/Public", redirect)
    app.router.add_get("/jellyfin/System/Info/Public", public_info)
    app.router.add_post("/Users/AuthenticateByName", authenticate)
    app.router.add_get("/Sessions", sessions)
    app.router.add_post("/Sessions/{session_id}/Playing", no_content)
    app.router.add_post("/Sessions/{session_id}/Playing/{command}", no_content)
    app.router.add_post("/Sessions/{session_id}/Command", no_content)
    app.router.add_get("/ScheduledTasks", admin_only)
    app.router.add_get("/Items/{item_id}/Download", download)
    app.router.add_post("/Items/{item_id}/Images/{image_type}", no_content)
    app.router.add_post("/Audio/{item_id}/Lyrics", lyrics)
    app.router.add_get("/socket", socket)
    return app


@pytest.fixture
async def fake_server() -> AsyncIterator[FakeServer]:
    fake = FakeServer()
    fake.server = TestServer(build_app(fake))
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@dataclass
class Stack:
    """One client stack plus transport adapter, driven from async tests.

    Sync calls run in a worker thread so the fake server keeps serving.
    """

    name: str
    transport: Any
    is_async: bool

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if self.is_async:
            return await func(*args, **kwargs)
        return await asyncio.to_thread(func, *args, **kwargs)

    def client(self, url: str, **kwargs: Any) -> Any:
        cls = AsyncJellyfinClient if self.is_async else JellyfinClient
        return cls(self.transport, url, CLIENT_INFO, **kwargs)

    async def connect(self, address: str, **kwargs: Any) -> Any:
        func = async_connect_to_address if self.is_async else connect_to_address
        return await self.call(func, self.transport, address, **kwargs)

    def websocket(self, client: Any, on_message: Callable[[Any], None], **kwargs: Any) -> Any:
        cls = AsyncJellyfinWebSocket if self.is_async else JellyfinWebSocket
        return cls(client, on_message, **kwargs)

    async def read_stream(self, client: Any, url: str, limit: int | None = None) -> bytes:
        """Read a download through client.stream(), stopping after ``limit`` bytes."""
        if self.is_async:
            data = b""
            async with client.stream(url) as response:
                async for chunk in response.iter_bytes(4096):
                    data += chunk
                    if limit is not None and len(data) >= limit:
                        break
            return data

        def read() -> bytes:
            data = b""
            with client.stream(url) as response:
                for chunk in response.iter_bytes(4096):
                    data += chunk
                    if limit is not None and len(data) >= limit:
                        break
            return data

        return await asyncio.to_thread(read)

    def on_connect(self, record: Callable[[], None]) -> Callable[[], Any]:
        """Wrap a recorder as the connect callback this stack expects."""
        if not self.is_async:
            return record

        async def callback() -> None:
            record()

        return callback


@pytest.fixture(params=["aiohttp", "httpx-async", "httpx-sync"])
async def stack(request: pytest.FixtureRequest) -> AsyncIterator[Stack]:
    if request.param == "aiohttp":
        async with aiohttp.ClientSession() as session:
            yield Stack(request.param, AiohttpTransport(session), is_async=True)
    elif request.param == "httpx-async":
        async with httpx.AsyncClient() as async_client:
            yield Stack(request.param, HttpxAsyncTransport(async_client), is_async=True)
    else:
        with httpx.Client() as sync_client:
            yield Stack(request.param, HttpxTransport(sync_client), is_async=False)


@pytest.fixture
def client(fake_server: FakeServer, stack: Stack) -> Any:
    return stack.client(fake_server.url, token=TOKEN, user_id=USER_ID)


async def stop_websocket(stack: Stack, ws: Any, task: Awaitable[Any]) -> None:
    """Stop a running websocket and check run() returns promptly."""
    await stack.call(ws.stop)
    async with asyncio.timeout(5):
        await task
