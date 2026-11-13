"""Live probe of the sync stack in the shape jellyfin-mpv-shim uses it.

A "player" client registers as a remote-controllable device and listens on
the WebSocket in a thread, while REST calls run on the main thread over the
same httpx client. A second "controller" client sends it the commands that
would normally come from Jellyfin Web.

    JF_URL=http://host:8096 JF_API_KEY=... python tools/shim_probe.py

Set JF_USER/JF_PASSWORD instead of JF_API_KEY to run as a user; SyncPlay
needs a user session.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
import uuid
from typing import Any

import httpx

from jellyfin_client import ClientInfo, JellyfinClient, JellyfinWebSocket, connect_to_address
from jellyfin_client.adapters.httpx import HttpxTransport
from jellyfin_client.exceptions import JellyfinError

TICKS_PER_SECOND = 10_000_000
WAIT = 10.0

CAPABILITIES: dict[str, Any] = {
    "PlayableMediaTypes": ["Video", "Audio"],
    "SupportedCommands": [
        "SetVolume",
        "Mute",
        "Unmute",
        "SetAudioStreamIndex",
        "SetSubtitleStreamIndex",
        "DisplayMessage",
        "PlayState",
        "PlayNext",
        "PlayMediaSource",
    ],
    "SupportsMediaControl": True,
}

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)
    return ok


class Player:
    """The shim side: WebSocket thread plus REST on the caller's thread."""

    def __init__(self, client: JellyfinClient) -> None:
        self.client = client
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.connects = 0
        self.connected = threading.Event()
        self.ws = JellyfinWebSocket(
            client, self._on_message, on_connect=self._on_connect, subscribe_sessions=False
        )
        self.thread = threading.Thread(target=self._run, name="jf-websocket")
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            self.ws.run()
        except BaseException as err:
            self.error = err

    def _on_connect(self) -> None:
        # Like the shim: (re)post capabilities on every connect, from the WS thread.
        self.client.post_full_capabilities(body=CAPABILITIES)
        self.connects += 1
        self.connected.set()

    def _on_message(self, message: Any) -> None:
        if message.get("MessageType") not in ("KeepAlive", "ForceKeepAlive"):
            self.messages.put(message)

    def wait_for(self, message_type: str, timeout: float = WAIT) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                break
            if message.get("MessageType") == message_type:
                return message
            print(f"      (other message: {message.get('MessageType')})", flush=True)
        return None


def make_client(transport: HttpxTransport, url: str, name: str) -> JellyfinClient:
    info = ClientInfo("shim-probe", "0.1", name, f"shim-probe-{name}-{uuid.uuid4().hex[:8]}")
    client = JellyfinClient(transport, url, info, token=os.environ.get("JF_API_KEY"))
    if not client.token:
        client.authenticate(os.environ["JF_USER"], os.environ["JF_PASSWORD"])
    return client


def find_session(controller: JellyfinClient, device_id: str) -> dict[str, Any] | None:
    sessions = controller.get_sessions(device_id=device_id)
    return sessions[0] if sessions else None


def pick_video(client: JellyfinClient, user_id: str | None) -> dict[str, Any]:
    result = client.get_items(
        user_id=user_id,
        recursive=True,
        include_item_types=["Movie", "Episode"],
        sort_by=["Random"],
        limit=1,
        fields=["MediaSources"],
    )
    return result["Items"][0]


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    address = os.environ["JF_URL"]
    # One httpx client shared by both threads, as the shim would do.
    with httpx.Client(http2=True) as http:
        transport = HttpxTransport(http)
        url, server = connect_to_address(transport, address)
        check("connect_to_address", bool(server.get("Id")), f"{url} {server.get('Version')}")

        player_client = make_client(transport, url, "player")
        controller = make_client(transport, url, "controller")
        player = Player(player_client)
        player.thread.start()
        try:
            run_checks(player, controller)
        finally:
            started = time.monotonic()
            player.ws.stop()
            player.thread.join(5)
            check(
                "stop() from main thread ends the WebSocket thread",
                not player.thread.is_alive(),
                f"{time.monotonic() - started:.2f}s",
            )
            if player.error:
                check("WebSocket thread raised nothing", False, repr(player.error))

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


def run_checks(player: Player, controller: JellyfinClient) -> None:
    client = player.client
    if not check("WebSocket connected, capabilities posted", player.connected.wait(WAIT)):
        return
    device_id = client.client_info.device_id
    session = find_session(controller, device_id)
    if not check("session visible to controller", session is not None):
        return
    check(
        "session is remote-controllable",
        bool(session.get("SupportsRemoteControl")),
        f"user={session.get('UserName')!r} media={session.get('PlayableMediaTypes')}",
    )
    session_id = session["Id"]
    user_id = session.get("UserId") if session.get("UserId") != "0" * 32 else None
    # An API key session has no user, but PlaybackInfo and user data need one.
    data_user_id = user_id or next(
        (
            u["Id"]
            for u in controller.get_users()
            if u.get("Name") == os.environ.get("JF_AS_USER", "testuser")
        ),
        None,
    )

    item = pick_video(controller, data_user_id)
    item_id = item["Id"]
    print(f"      item: {item.get('Name')} ({item_id})", flush=True)

    # Remote "play" as from Jellyfin Web's cast menu.
    controller.remote.play_media(session_id, [item_id])
    play = player.wait_for("Play")
    if not check("Play message received", play is not None):
        return
    data = play.get("Data") or {}
    check(
        "Play carries the item",
        item_id in (data.get("ItemIds") or []),
        str(data.get("PlayCommand")),
    )

    info = client.get_posted_playback_info(
        item_id,
        user_id=data_user_id,
        body={
            "DeviceProfile": {
                "DirectPlayProfiles": [{"Type": "Video"}, {"Type": "Audio"}],
                "TranscodingProfiles": [],
            },
            "AutoOpenLiveStream": True,
        },
    )
    sources = info.get("MediaSources") or []
    check(
        "PlaybackInfo returns media sources",
        bool(sources),
        f"PlaySessionId={info.get('PlaySessionId')}",
    )
    if not sources:
        return
    source = sources[0]
    play_session_id = info.get("PlaySessionId")

    stream_url = client.video_url(item_id, media_source_id=source["Id"], include_token=False)
    received = 0
    with client.stream(stream_url) as response:
        for chunk in response.iter_bytes(65536):
            received += len(chunk)
            if received >= 1 << 20:
                break
    check("stream() reads the first MiB", received >= 1 << 20, f"{received} bytes")

    base = {
        "ItemId": item_id,
        "MediaSourceId": source["Id"],
        "PlaySessionId": play_session_id,
        "CanSeek": True,
        "PlayMethod": "DirectPlay",
    }
    client.report_playback_start(body={**base, "PositionTicks": 0})
    for seconds in (10, 20):
        client.report_playback_progress(
            body={**base, "PositionTicks": seconds * TICKS_PER_SECOND, "IsPaused": False}
        )
    now_playing = (find_session(controller, player.client.client_info.device_id) or {}).get(
        "NowPlayingItem"
    )
    check(
        "controller sees NowPlayingItem",
        bool(now_playing) and now_playing.get("Id") == item_id,
        str(now_playing and now_playing.get("Name")),
    )

    controller.remote.pause(session_id)
    message = player.wait_for("Playstate")
    check(
        "Playstate Pause received",
        message is not None and message["Data"].get("Command") == "Pause",
    )
    client.report_playback_progress(
        body={**base, "PositionTicks": 20 * TICKS_PER_SECOND, "IsPaused": True}
    )

    controller.remote.seek(session_id, 42 * TICKS_PER_SECOND)
    message = player.wait_for("Playstate")
    check(
        "Playstate Seek received",
        message is not None and message["Data"].get("SeekPositionTicks") == 42 * TICKS_PER_SECOND,
    )

    controller.remote.set_volume(session_id, 35)
    message = player.wait_for("GeneralCommand")
    check(
        "GeneralCommand SetVolume received",
        message is not None and message["Data"].get("Arguments", {}).get("Volume") == "35",
    )

    # REST from the main thread while the WS thread is busy receiving.
    errors: list[BaseException] = []

    def hammer() -> None:
        try:
            for _ in range(20):
                client.get_system_info()
        except BaseException as err:
            errors.append(err)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    check("80 concurrent REST calls on the shared client", not errors, repr(errors[:1]))

    client.report_playback_stopped(
        body={
            "ItemId": item_id,
            "MediaSourceId": source["Id"],
            "PlaySessionId": play_session_id,
            "PositionTicks": 42 * TICKS_PER_SECOND,
        }
    )
    now_playing = (find_session(controller, player.client.client_info.device_id) or {}).get(
        "NowPlayingItem"
    )
    check("NowPlayingItem cleared after stop", not now_playing)

    run_reconnect(player, controller, session_id)

    if user_id:
        run_syncplay(player, client)
    else:
        print("      SyncPlay skipped: API key session has no user", flush=True)


def run_reconnect(player: Player, controller: JellyfinClient, session_id: str) -> None:
    """Drop the connection under the receive loop, as a network outage would."""
    before = player.connects
    player.connected.clear()
    connection = player.ws._connection
    if connection is None:
        check("WebSocket reconnects after a drop", False, "no open connection")
        return
    started = time.monotonic()
    connection.close()
    reconnected = player.connected.wait(WAIT)
    check(
        "WebSocket reconnects after a drop, capabilities re-posted",
        reconnected and player.connects == before + 1,
        f"{time.monotonic() - started:.2f}s",
    )
    # The server must route commands to the new connection.
    controller.remote.unpause(session_id)
    message = player.wait_for("Playstate")
    check(
        "commands arrive after reconnect",
        message is not None and message["Data"].get("Command") == "Unpause",
    )


def run_syncplay(player: Player, client: JellyfinClient) -> None:
    try:
        client.sync_play_create_group(body={"GroupName": "shim-probe"})
    except JellyfinError as err:
        check("SyncPlay create group", False, repr(err))
        return
    message = player.wait_for("SyncPlayGroupUpdate")
    check(
        "SyncPlayGroupUpdate received",
        message is not None,
        str(message and message["Data"].get("Type")),
    )
    client.sync_play_ping(body={"Ping": 25})
    client.sync_play_leave_group()
    message = player.wait_for("SyncPlayGroupUpdate")
    check(
        "SyncPlay leave acknowledged",
        message is not None,
        str(message and message["Data"].get("Type")),
    )


if __name__ == "__main__":
    sys.exit(main())
