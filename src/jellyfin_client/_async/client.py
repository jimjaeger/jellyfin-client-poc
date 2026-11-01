"""Jellyfin client and server address probing."""

from __future__ import annotations

from typing import Any, cast

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
from ..transport import AsyncTransport


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
    ) -> Any:
        request = self._prepare(method, path, params, json)
        response = await self.transport.send(request, self.timeout)
        return self._decode(response, method, path)

    async def authenticate(self, username: str, password: str) -> models.AuthenticationResult:
        """Log in with username and password and keep the access token."""
        result = await self.authenticate_user_by_name(body={"Username": username, "Pw": password})
        self._store_login(result)
        return result

    async def remote_playstate(
        self,
        session_id: str,
        command: models.PlaystateCommand,
        *,
        seek_position_ticks: int | None = None,
    ) -> None:
        """Send a playstate command such as Pause, Unpause, Stop or Seek."""
        await self.send_playstate_command(
            session_id, command, seek_position_ticks=seek_position_ticks
        )

    async def remote_play_media(
        self,
        session_id: str,
        item_ids: list[str],
        command: models.PlayCommand = "PlayNow",
    ) -> None:
        """Start playback of items on a session."""
        await self.play(session_id, play_command=command, item_ids=item_ids)

    async def remote_command(
        self,
        session_id: str,
        command: models.GeneralCommandType | str,
        arguments: dict[str, str] | None = None,
    ) -> None:
        """Send a general command such as Mute or SetVolume.

        Any command name is passed through, so clients can be sent commands
        newer than this library's spec.
        """
        body: models.GeneralCommand = {"Name": cast("models.GeneralCommandType", command)}
        if arguments:
            body["Arguments"] = dict(arguments)
        await self.send_full_general_command(session_id, body=body)

    async def remote_set_volume(self, session_id: str, volume: int) -> None:
        """Set the volume of a session, 0 to 100."""
        # The server models command arguments as strings.
        await self.remote_command(session_id, "SetVolume", {"Volume": str(volume)})
