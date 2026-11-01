"""Client logic shared by the sync and async stacks; no I/O happens here."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from .exceptions import (
    JellyfinApiError,
    JellyfinAuthError,
    JellyfinConnectionError,
    JellyfinError,
    JellyfinForbiddenError,
    JellyfinNotFoundError,
)
from .generated import models
from .generated.urls import GeneratedUrls
from .transport import Request, Response

DEFAULT_TIMEOUT = 10.0

# Access token query parameter for URLs handed to other players. The legacy
# "api_key" spelling is off by default from Jellyfin 12.
APIKEY_PARAM = "ApiKey"

PUBLIC_INFO_PATH = "/System/Info/Public"


@dataclass(frozen=True, slots=True)
class ClientInfo:
    """Identifies this client and device to the server."""

    client: str
    version: str
    device: str
    device_id: str


def _encode_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        # Jellyfin takes array query parameters comma delimited.
        return ",".join(_encode_value(v) for v in value)
    return str(value)


def encode_params(params: Mapping[str, Any]) -> dict[str, str]:
    """Drop unset parameters and encode the rest the way Jellyfin expects."""
    return {key: _encode_value(value) for key, value in params.items() if value is not None}


def normalize_address(address: str) -> str:
    """Turn a user-entered server address into a base URL."""
    url = address.strip().rstrip("/")
    if "://" not in url:
        url = f"http://{url}"
    return url


def public_info_request(url: str) -> Request:
    """Request for the unauthenticated server info used to probe an address."""
    return Request("GET", f"{url}{PUBLIC_INFO_PATH}", headers={"Accept": "application/json"})


def parse_public_info(response: Response, url: str) -> tuple[str, models.PublicSystemInfo]:
    """Return the base URL after redirects and the public server info."""
    if response.status != 200:
        raise JellyfinConnectionError(f"{url} answered with HTTP {response.status}")
    try:
        info: models.PublicSystemInfo = json.loads(response.body)
    except ValueError as err:
        raise JellyfinConnectionError(f"{url} is not a Jellyfin server") from err
    if not isinstance(info, dict) or not info.get("Id"):
        raise JellyfinConnectionError(f"{url} is not a Jellyfin server")
    return response.url.removesuffix(PUBLIC_INFO_PATH), info


class ClientBase(GeneratedUrls):
    """State, authentication header, URL building and response decoding."""

    def __init__(
        self,
        url: str,
        client_info: ClientInfo,
        *,
        token: str | None = None,
        user_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.url = normalize_address(url)
        self.client_info = client_info
        self.token = token
        self.user_id = user_id
        self.timeout = timeout

    @property
    def authorization(self) -> str:
        """Value of the Authorization header for this client."""
        info = self.client_info
        parts = {
            "Client": info.client,
            "Device": info.device,
            "DeviceId": info.device_id,
            "Version": info.version,
        }
        if self.token:
            parts["Token"] = self.token
        # Values are percent-encoded like the official TypeScript SDK does, so
        # quotes or commas in a device name cannot break the header.
        fields = ", ".join(f'{key}="{quote(value, safe=" ")}"' for key, value in parts.items())
        return f"MediaBrowser {fields}"

    @property
    def websocket_url(self) -> str:
        """WebSocket URL derived from the server URL."""
        return f"{self.url.replace('http', 'ws', 1)}/socket"

    def _prepare(
        self, method: str, path: str, params: dict[str, Any] | None, json_body: Any
    ) -> Request:
        return Request(
            method,
            f"{self.url}{path}",
            params=encode_params(params) if params else {},
            headers={"Authorization": self.authorization, "Accept": "application/json"},
            json=json_body,
        )

    @staticmethod
    def _decode(response: Response, method: str, path: str) -> Any:
        status = response.status
        if status == 401:
            raise JellyfinAuthError(f"{method} {path} was rejected with HTTP 401")
        if status == 403:
            raise JellyfinForbiddenError(f"{method} {path} is not permitted for this user")
        if status == 404:
            raise JellyfinNotFoundError(f"{method} {path} not found")
        if status >= 400:
            raise JellyfinApiError(status, method, path)
        if not response.body:
            return None
        try:
            return json.loads(response.body)
        except ValueError as err:
            raise JellyfinError("Response body is not JSON") from err

    def _store_login(self, result: models.AuthenticationResult) -> None:
        token = result.get("AccessToken")
        user = result.get("User")
        if not token or not user:
            raise JellyfinAuthError("Server returned no access token")
        self.token = token
        self.user_id = user.get("Id")

    def build_url(self, path: str, params: Mapping[str, Any], *, include_token: bool) -> str:
        """Build an absolute URL for a client outside this library to fetch."""
        query = encode_params(params)
        if include_token and self.token:
            query[APIKEY_PARAM] = self.token
        return f"{self.url}{path}?{urlencode(query)}" if query else f"{self.url}{path}"

    def image_url(
        self,
        item_id: str,
        image_type: models.ImageType = "Primary",
        *,
        max_width: int | None = None,
        include_token: bool = True,
    ) -> str:
        """URL of an item image."""
        return self.get_item_image_url(
            item_id, image_type, max_width=max_width, include_token=include_token
        )

    def audio_url(
        self,
        item_id: str,
        *,
        container: str | None = None,
        audio_codec: str | None = None,
        max_streaming_bitrate: int = 140_000_000,
        include_token: bool = True,
    ) -> str:
        """URL of the universal audio stream of an item, for this user and device."""
        return self.get_universal_audio_stream_url(
            item_id,
            user_id=self.user_id,
            device_id=self.client_info.device_id,
            max_streaming_bitrate=max_streaming_bitrate,
            container=[container] if container else None,
            audio_codec=audio_codec,
            include_token=include_token,
        )

    def video_url(
        self, item_id: str, *, media_source_id: str | None = None, include_token: bool = True
    ) -> str:
        """URL of the static video stream of an item, for this device."""
        return self.get_video_stream_url(
            item_id,
            static=True,
            device_id=self.client_info.device_id,
            media_source_id=media_source_id,
            include_token=include_token,
        )
