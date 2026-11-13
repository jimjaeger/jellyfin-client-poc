"""Find Jellyfin servers on the local network.

Servers with auto discovery enabled answer a UDP query on port 7359 with a
short JSON description. Nothing in a reply is authenticated: any device on
the network can answer with any name and address. Show the addresses to the
user to pick from; do not log in to one automatically.

Network errors are logged, not raised: discovery is a convenience, and an
empty result is the answer when it fails.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from typing import Any, NotRequired, TypedDict

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PORT = 7359
DISCOVERY_QUERY = b"who is JellyfinServer?"
BROADCAST = ("255.255.255.255", DISCOVERY_PORT)


class DiscoveredServer(TypedDict):
    """A server's answer to the discovery query."""

    Id: str
    Name: str
    Address: str
    EndpointAddress: NotRequired[str | None]


class _Collector:
    """Keep the first valid reply per server id, in arrival order."""

    def __init__(self) -> None:
        self.servers: list[DiscoveredServer] = []
        self._ids: set[str] = set()

    def add(self, data: bytes, sender: Any) -> None:
        try:
            reply = json.loads(data)
        except ValueError:
            _LOGGER.debug("Ignoring a discovery reply from %s that is not JSON", sender)
            return
        if not (
            isinstance(reply, dict)
            and isinstance(reply.get("Id"), str)
            and isinstance(reply.get("Address"), str)
        ):
            _LOGGER.debug("Ignoring a discovery reply from %s without Id or Address", sender)
            return
        if reply["Id"] in self._ids:
            return
        self._ids.add(reply["Id"])
        reply.setdefault("Name", reply["Address"])
        self.servers.append(reply)  # type: ignore[arg-type]


def discover_servers(
    timeout: float = 1.0, address: tuple[str, int] = BROADCAST
) -> list[DiscoveredServer]:
    """Send the discovery query and collect replies for ``timeout`` seconds.

    Blocks for the whole timeout, since no reply says it is the last one.
    ``address`` defaults to the local broadcast address; a unicast address
    asks one host.
    """
    collector = _Collector()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as err:
        _LOGGER.warning("Server discovery could not open a socket: %s", err)
        return []
    with sock:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(DISCOVERY_QUERY, address)
        except OSError as err:
            _LOGGER.warning("Server discovery could not send its query: %s", err)
            return []
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                data, sender = sock.recvfrom(65535)
            except TimeoutError:
                break
            except ConnectionResetError:
                # Windows reports an ICMP "port unreachable" for the query on
                # a later receive; replies from other hosts may still come.
                continue
            except OSError as err:
                _LOGGER.warning("Server discovery stopped receiving: %s", err)
                break
            collector.add(data, sender)
    return collector.servers


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, collector: _Collector) -> None:
        self._collector = collector

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self._collector.add(data, addr)

    def error_received(self, exc: Exception) -> None:
        # See discover_servers: an ICMP error for the query is not fatal.
        _LOGGER.debug("Server discovery received an error: %s", exc)


async def async_discover_servers(
    timeout: float = 1.0, address: tuple[str, int] = BROADCAST
) -> list[DiscoveredServer]:
    """Async version of :func:`discover_servers`, without blocking the loop."""
    collector = _Collector()
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DiscoveryProtocol(collector),
            family=socket.AF_INET,
            allow_broadcast=True,
        )
    except OSError as err:
        _LOGGER.warning("Server discovery could not open a socket: %s", err)
        return []
    try:
        transport.sendto(DISCOVERY_QUERY, address)
        await asyncio.sleep(timeout)
    except OSError as err:
        _LOGGER.warning("Server discovery could not send its query: %s", err)
    finally:
        transport.close()
    return collector.servers
