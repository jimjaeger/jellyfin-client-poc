"""Exceptions raised by the Jellyfin client."""


class JellyfinError(Exception):
    """Base class for all Jellyfin client errors."""


class JellyfinConnectionError(JellyfinError):
    """The server could not be reached or did not answer in time."""


class JellyfinAuthError(JellyfinError):
    """The server rejected the credentials or the access token."""


class JellyfinForbiddenError(JellyfinError):
    """The token is valid but lacks permission, e.g. an admin-only request."""


class JellyfinNotFoundError(JellyfinError):
    """The requested resource does not exist."""


class JellyfinApiError(JellyfinError):
    """The server answered with an unexpected error status."""

    def __init__(self, status: int, method: str, path: str) -> None:
        super().__init__(f"{method} {path} failed with HTTP {status}")
        self.status = status
