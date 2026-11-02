"""Remote control of client sessions, available as ``client.remote``."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ..generated import models

if TYPE_CHECKING:
    from .client import AsyncJellyfinClient


class AsyncRemoteControl:
    """Playback and general commands for a session, built on the generated endpoints."""

    def __init__(self, client: AsyncJellyfinClient) -> None:
        self._client = client

    async def pause(self, session_id: str) -> None:
        await self._client.send_playstate_command(session_id, "Pause")

    async def unpause(self, session_id: str) -> None:
        await self._client.send_playstate_command(session_id, "Unpause")

    async def play_pause(self, session_id: str) -> None:
        await self._client.send_playstate_command(session_id, "PlayPause")

    async def stop(self, session_id: str) -> None:
        await self._client.send_playstate_command(session_id, "Stop")

    async def seek(self, session_id: str, position_ticks: int) -> None:
        """Seek to a position; Jellyfin ticks are 100 ns."""
        await self._client.send_playstate_command(
            session_id, "Seek", seek_position_ticks=position_ticks
        )

    async def play_media(
        self, session_id: str, item_ids: list[str], command: models.PlayCommand = "PlayNow"
    ) -> None:
        """Start playback of items on a session."""
        await self._client.play(session_id, play_command=command, item_ids=item_ids)

    async def command(
        self,
        session_id: str,
        name: models.GeneralCommandType | str,
        arguments: dict[str, str] | None = None,
    ) -> None:
        """Send a general command such as MoveUp or DisplayContent.

        Any command name is passed through, so clients can be sent commands
        newer than this library's spec.
        """
        body: models.GeneralCommand = {"Name": cast("models.GeneralCommandType", name)}
        if arguments:
            body["Arguments"] = dict(arguments)
        await self._client.send_full_general_command(session_id, body=body)

    async def set_volume(self, session_id: str, volume: int) -> None:
        """Set the volume, 0 to 100."""
        # The server models command arguments as strings.
        await self.command(session_id, "SetVolume", {"Volume": str(volume)})

    async def mute(self, session_id: str) -> None:
        await self.command(session_id, "Mute")

    async def unmute(self, session_id: str) -> None:
        await self.command(session_id, "Unmute")
