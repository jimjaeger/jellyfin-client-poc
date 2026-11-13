"""Tests for UDP server discovery against a local responder."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from jellyfin_client import async_discover_servers, discover_servers
from jellyfin_client.discovery import DISCOVERY_QUERY

SERVER = {"Id": "1" * 32, "Name": "Living room", "Address": "http://10.0.0.5:8096"}
OTHER = {"Id": "2" * 32, "Name": "Attic", "Address": "https://10.0.0.6:8920"}


class Responder:
    """Answers each query on 127.0.0.1 with a fixed list of datagrams."""

    def __init__(self, replies: list[bytes]) -> None:
        self.replies = replies
        self.queries: list[bytes] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.address: tuple[str, int] = self.sock.getsockname()
        self._stopped = False
        self._thread = threading.Thread(target=self._serve)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stopped:
            try:
                data, sender = self.sock.recvfrom(1024)
            except TimeoutError:
                continue
            self.queries.append(data)
            for reply in self.replies:
                self.sock.sendto(reply, sender)

    def close(self) -> None:
        self._stopped = True
        self._thread.join()
        self.sock.close()


@pytest.fixture
def responder() -> Iterator[Callable[[list[bytes]], Responder]]:
    started: list[Responder] = []

    def start(replies: list[bytes]) -> Responder:
        started.append(Responder(replies))
        return started[-1]

    yield start
    for item in started:
        item.close()


@pytest.fixture(params=["sync", "async"])
def discover(request: pytest.FixtureRequest) -> Callable[..., Any]:
    """Run the sync or the async variant with the same arguments."""
    if request.param == "async":
        return async_discover_servers

    async def run_sync(*args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(discover_servers, *args, **kwargs)

    return run_sync


async def test_replies_are_collected_once_per_server(
    responder: Callable[[list[bytes]], Responder], discover: Callable[..., Any]
) -> None:
    server = responder(
        [
            json.dumps(SERVER).encode(),
            b"not json",
            json.dumps({"Name": "no id or address"}).encode(),
            json.dumps(SERVER).encode(),
            json.dumps({**OTHER, "EndpointAddress": None}).encode(),
        ]
    )

    found = await discover(timeout=0.3, address=server.address)

    assert found == [SERVER, {**OTHER, "EndpointAddress": None}]
    assert server.queries == [DISCOVERY_QUERY]


async def test_name_defaults_to_address(
    responder: Callable[[list[bytes]], Responder], discover: Callable[..., Any]
) -> None:
    server = responder([json.dumps({"Id": "3" * 32, "Address": "http://h:8096"}).encode()])

    found = await discover(timeout=0.3, address=server.address)

    assert found == [{"Id": "3" * 32, "Address": "http://h:8096", "Name": "http://h:8096"}]


async def test_no_answer_returns_empty(discover: Callable[..., Any]) -> None:
    # Nothing listens on this port; on Linux the ICMP error must not raise.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    address = sock.getsockname()
    sock.close()

    assert await discover(timeout=0.2, address=address) == []


async def test_unusable_address_returns_empty(discover: Callable[..., Any]) -> None:
    assert await discover(timeout=0.2, address=("256.0.0.1", 7359)) == []
