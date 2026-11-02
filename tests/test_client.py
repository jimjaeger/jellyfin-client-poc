"""Tests for the REST client, run against every stack and adapter."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from jellyfin_client import (
    JellyfinAuthError,
    JellyfinConnectionError,
    JellyfinForbiddenError,
    JellyfinNotFoundError,
)

from .conftest import PUBLIC_INFO, TOKEN, USER_ID, FakeServer, Stack


async def test_connect_to_address(fake_server: FakeServer, stack: Stack) -> None:
    url, info = await stack.connect(fake_server.url + "/")

    assert url == fake_server.url
    assert info == PUBLIC_INFO


async def test_connect_to_address_follows_redirect(fake_server: FakeServer, stack: Stack) -> None:
    url, _ = await stack.connect(fake_server.url + "/old")

    assert url == fake_server.url + "/jellyfin"


async def test_connect_to_address_unreachable(stack: Stack) -> None:
    with pytest.raises(JellyfinConnectionError):
        await stack.connect("127.0.0.1:1", request_timeout=2)


async def test_connect_to_address_not_jellyfin(fake_server: FakeServer, stack: Stack) -> None:
    with pytest.raises(JellyfinConnectionError):
        await stack.connect(fake_server.url + "/missing")


async def test_authenticate(fake_server: FakeServer, stack: Stack) -> None:
    client = stack.client(fake_server.url)

    await stack.call(client.authenticate, "alice", "pw")

    assert client.token == TOKEN
    assert client.user_id == USER_ID
    header = fake_server.requests[-1]["headers"]["Authorization"]
    # Quotes and commas in the device name are percent-encoded.
    assert header == (
        'MediaBrowser Client="Home Assistant", Device="Living room%2C %22TV%22", '
        'DeviceId="dev-1", Version="1.0"'
    )


async def test_authenticate_rejected(fake_server: FakeServer, stack: Stack) -> None:
    client = stack.client(fake_server.url)

    with pytest.raises(JellyfinAuthError):
        await stack.call(client.authenticate, "alice", "wrong")
    assert client.token is None


async def test_get_sessions_passes_unknown_values_through(
    fake_server: FakeServer, stack: Stack, client: Any
) -> None:
    sessions = await stack.call(client.get_sessions, active_within_seconds=960)

    assert sessions[0]["PlayState"]["PlayMethod"] == "SomethingNew"
    assert sessions[0]["FieldFromTheFuture"] == 1
    request = fake_server.requests[-1]
    assert request["query"] == {"activeWithinSeconds": "960"}
    assert f'Token="{TOKEN}"' in request["headers"]["Authorization"]


async def test_invalid_token(stack: Stack, client: Any) -> None:
    client.token = "expired"

    with pytest.raises(JellyfinAuthError):
        await stack.call(client.get_sessions)


async def test_forbidden_is_not_an_auth_error(stack: Stack, client: Any) -> None:
    # A valid token without admin rights must not look like an expired login.
    with pytest.raises(JellyfinForbiddenError):
        await stack.call(client.get_tasks)


async def test_not_found(stack: Stack, client: Any) -> None:
    with pytest.raises(JellyfinNotFoundError):
        await stack.call(client.get_item, "missing", user_id=USER_ID)


async def test_remote_commands(fake_server: FakeServer, stack: Stack, client: Any) -> None:
    await stack.call(client.remote_playstate, "session-1", "Seek", seek_position_ticks=50)
    await stack.call(client.remote_play_media, "session-1", ["a", "b"])
    await stack.call(client.remote_set_volume, "session-1", 40)
    await stack.call(client.remote_command, "session-1", "Mute")

    seek, play, volume, mute = fake_server.requests
    assert (seek["path"], seek["query"]) == (
        "/Sessions/session-1/Playing/Seek",
        {"seekPositionTicks": "50"},
    )
    assert (play["path"], play["query"]) == (
        "/Sessions/session-1/Playing",
        {"playCommand": "PlayNow", "itemIds": "a,b"},
    )
    assert volume["json"] == {"Name": "SetVolume", "Arguments": {"Volume": "40"}}
    assert mute["json"] == {"Name": "Mute"}


async def test_path_parameters_are_quoted(
    fake_server: FakeServer, stack: Stack, client: Any
) -> None:
    await stack.call(client.remote_playstate, "a/b", "Pause")

    assert fake_server.requests[-1]["raw_path"] == "/Sessions/a%2Fb/Playing/Pause"


@pytest.mark.parametrize(
    ("method", "kwargs", "path", "query"),
    [
        pytest.param(
            "image_url",
            {"max_width": 300},
            "/Items/item-1/Images/Primary",
            {"maxWidth": ["300"], "ApiKey": [TOKEN]},
            id="image",
        ),
        pytest.param(
            "audio_url",
            {},
            "/Audio/item-1/universal",
            {
                "userId": [USER_ID],
                "deviceId": ["dev-1"],
                "maxStreamingBitrate": ["140000000"],
                "ApiKey": [TOKEN],
            },
            id="audio",
        ),
        pytest.param(
            "video_url",
            {},
            "/Videos/item-1/stream",
            {"static": ["true"], "deviceId": ["dev-1"], "ApiKey": [TOKEN]},
            id="video",
        ),
    ],
)
def test_url_builders(
    client: Any,
    method: str,
    kwargs: dict[str, int],
    path: str,
    query: dict[str, list[str]],
) -> None:
    url = urlsplit(getattr(client, method)("item-1", **kwargs))

    assert url.path == path
    assert parse_qs(url.query) == query


def test_generated_url_builder(client: Any) -> None:
    url = urlsplit(client.get_trickplay_tile_image_url("item-1", 320, 5, media_source_id="ms"))

    assert url.path == "/Videos/item-1/Trickplay/320/5.jpg"
    assert parse_qs(url.query) == {"mediaSourceId": ["ms"], "ApiKey": [TOKEN]}


def test_generated_url_builder_without_token(client: Any) -> None:
    url = client.get_item_image_url("item-1", "Backdrop", include_token=False)

    assert url.endswith("/Items/item-1/Images/Backdrop")


async def test_image_upload_is_base64(fake_server: FakeServer, stack: Stack, client: Any) -> None:
    image = b"\x89PNG\r\n\x1a\nfake image"

    await stack.call(
        client.set_item_image, "item-1", "Primary", body=image, content_type="image/png"
    )

    request = fake_server.requests[-1]
    assert request["path"] == "/Items/item-1/Images/Primary"
    assert request["content_type"] == "image/png"
    # The server decodes image uploads as base64, although the spec says binary.
    assert base64.b64decode(request["raw"]) == image


async def test_text_upload_is_raw(fake_server: FakeServer, stack: Stack, client: Any) -> None:
    result = await stack.call(
        client.upload_lyrics, "item-1", file_name="song.lrc", body="[00:01.00] Hello"
    )

    request = fake_server.requests[-1]
    assert request["query"] == {"fileName": "song.lrc"}
    assert request["content_type"] == "text/plain"
    assert request["raw"] == b"[00:01.00] Hello"
    assert result == {"Lyrics": [{"Text": "[00:01.00] Hello"}]}
