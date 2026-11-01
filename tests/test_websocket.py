"""Tests for the WebSocket connection, run against every stack and adapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pytest

from jellyfin_client import JellyfinAuthError

from .conftest import SESSIONS, TOKEN, FakeServer, Stack, stop_websocket


async def wait_for(condition: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def test_messages_keepalive_and_resync(
    fake_server: FakeServer, stack: Stack, client: Any
) -> None:
    fake_server.ws_script = [
        {"MessageType": "ForceKeepAlive", "Data": 60},
        {"MessageType": "Sessions", "Data": SESSIONS},
        {"MessageType": "SomethingFromTheFuture"},
    ]
    received: list[Any] = []
    connects: list[None] = []

    ws = stack.websocket(
        client, received.append, on_connect=stack.on_connect(lambda: connects.append(None))
    )
    task = asyncio.create_task(stack.call(ws.run))
    await wait_for(lambda: len(received) == 3)

    assert [m["MessageType"] for m in received] == [
        "ForceKeepAlive",
        "Sessions",
        "SomethingFromTheFuture",
    ]
    await wait_for(lambda: len(fake_server.ws_messages) == 2)
    assert fake_server.ws_messages == [
        {"MessageType": "SessionsStart", "Data": "0,1500"},
        {"MessageType": "KeepAlive"},
    ]
    assert len(connects) == 1
    await stop_websocket(stack, ws, task)


async def test_keepalive_is_repeated(fake_server: FakeServer, stack: Stack, client: Any) -> None:
    # A 2 s server timeout means a KeepAlive at least every second.
    fake_server.ws_script = [{"MessageType": "ForceKeepAlive", "Data": 2}]

    ws = stack.websocket(client, lambda _: None, subscribe_sessions=False)
    task = asyncio.create_task(stack.call(ws.run))
    await wait_for(lambda: len(fake_server.ws_messages) >= 3)

    assert {m["MessageType"] for m in fake_server.ws_messages} == {"KeepAlive"}
    await stop_websocket(stack, ws, task)


async def test_reconnects_after_server_close(
    fake_server: FakeServer, stack: Stack, client: Any
) -> None:
    fake_server.close_ws_once = True
    connects: list[None] = []

    ws = stack.websocket(
        client,
        lambda _: None,
        on_connect=stack.on_connect(lambda: connects.append(None)),
        min_backoff=0.01,
    )
    task = asyncio.create_task(stack.call(ws.run))

    # Depending on timing the first connection closes before on_connect runs.
    await wait_for(lambda: fake_server.ws_connections == 2 and ws.connected and connects)
    await wait_for(lambda: len(fake_server.ws_messages) == 1)
    assert fake_server.ws_messages == [{"MessageType": "SessionsStart", "Data": "0,1500"}]
    await stop_websocket(stack, ws, task)


async def test_rejected_token_stops_reconnecting(
    fake_server: FakeServer, stack: Stack, client: Any
) -> None:
    fake_server.reject_ws = True
    ws = stack.websocket(client, lambda _: None, min_backoff=0.01)

    with pytest.raises(JellyfinAuthError):
        await stack.call(ws.run)
    assert fake_server.ws_connections == 1


async def test_stop_while_waiting_to_reconnect(stack: Stack, client: Any) -> None:
    client.url = "http://127.0.0.1:1"
    ws = stack.websocket(client, lambda _: None, min_backoff=60)
    task = asyncio.create_task(stack.call(ws.run))
    await asyncio.sleep(0.3)

    await stop_websocket(stack, ws, task)


async def test_callback_errors_do_not_drop_connection(
    fake_server: FakeServer, stack: Stack, client: Any
) -> None:
    fake_server.ws_script = [{"MessageType": "Sessions"}, {"MessageType": "UserDataChanged"}]
    received: list[str] = []

    def on_message(message: Any) -> None:
        received.append(message["MessageType"])
        if len(received) == 1:
            raise RuntimeError("boom")

    ws = stack.websocket(client, on_message)
    task = asyncio.create_task(stack.call(ws.run))
    await wait_for(lambda: len(received) == 2)

    assert received == ["Sessions", "UserDataChanged"]
    assert fake_server.ws_connections == 1
    await stop_websocket(stack, ws, task)


async def test_token_not_logged(
    fake_server: FakeServer, stack: Stack, client: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    ws = stack.websocket(client, lambda _: None)
    task = asyncio.create_task(stack.call(ws.run))
    await wait_for(lambda: ws.connected)
    await stop_websocket(stack, ws, task)

    assert TOKEN not in caplog.text
