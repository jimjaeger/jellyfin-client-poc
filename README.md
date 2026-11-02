# jellyfin-client-poc

Proof of concept for the hybrid client discussed in
[jellyfin-apiclient-python#101](https://github.com/jellyfin/jellyfin-apiclient-python/issues/101):
models and endpoint methods are generated from the Jellyfin OpenAPI spec; transport,
authentication, discovery and the WebSocket are written by hand. There is a sync and an
async stack, and the HTTP library is a pluggable adapter.

```python
# asyncio, e.g. Home Assistant
from jellyfin_client import AsyncJellyfinClient, ClientInfo, async_connect_to_address
from jellyfin_client.adapters.aiohttp import AiohttpTransport

transport = AiohttpTransport(session)
url, info = await async_connect_to_address(transport, "jellyfin.local:8096")
client = AsyncJellyfinClient(transport, url, ClientInfo("App", "1.0", "Device", "device-id"))
await client.authenticate("user", "password")
sessions = await client.get_sessions()

await client.remote.pause(session_id)

# uploads and downloads
await client.set_item_image(item_id, "Primary", body=png_bytes, content_type="image/png")
async with client.stream(client.get_download_url(item_id, include_token=False)) as response:
    async for chunk in response.iter_bytes(65536):
        ...

# threads, e.g. jellyfin-mpv-shim (HTTP/2 with the http2 extra)
from jellyfin_client import JellyfinClient, connect_to_address
from jellyfin_client.adapters.httpx import HttpxTransport

transport = HttpxTransport(httpx.Client(http2=True))
url, info = connect_to_address(transport, "https://jellyfin.example")
client = JellyfinClient(transport, url, ClientInfo("App", "1.0", "Device", "device-id"))

# download to a file, sync stack
with client.stream(client.get_download_url(item_id, include_token=False)) as response:
    with open("movie.mkv", "wb") as file:
        for chunk in response.iter_bytes(65536):
            file.write(chunk)
```

## Layout

All paths are below `src/jellyfin_client/`, except `spec/` and `tools/`.

| Path | Written | Content |
|---|---|---|
| `spec/jellyfin-openapi.json` | — | Stable spec 12.4.0 from repo.jellyfin.org |
| `tools/generate.py` | by hand | Generates models and async endpoints, then derives the sync stack with [unasync](https://github.com/python-trio/unasync) |
| `generated/models.py` | generated | 357 TypedDicts and `Literal` enum aliases, including the WebSocket messages |
| `generated/_async/endpoints.py` | generated | 299 async endpoint methods covering every tag, including uploads |
| `generated/_sync/endpoints.py` | unasync | Same methods, sync |
| `generated/urls.py` | generated | 35 URL builders for endpoints returning images, streams or files (shared by both stacks) |
| `transport.py` | by hand | `Request`/`Response` and the `AsyncTransport`/`SyncTransport` protocols that adapters implement |
| `_base.py` | by hand | Shared logic without I/O: auth header, URL builders, response decoding |
| `_async/client.py`, `_async/remote.py`, `_async/websocket.py` | by hand | Client, address probing, WebSocket (KeepAlive, reconnect with backoff and jitter, resync callback) |
| `_sync/` | unasync | Sync client and WebSocket generated from `_async/` |
| `adapters/aiohttp.py` | by hand | Async adapter on a caller-owned `aiohttp.ClientSession` |
| `adapters/httpx.py` | by hand | Sync and async adapters on httpx, WebSocket via `websockets` |

The core has no dependencies; each adapter needs its extra (`aiohttp`, `httpx`, `http2`).
Regenerate with `python tools/generate.py`.

## Design decisions

- **TypedDict instead of validated models.** The client returns the JSON unchanged, so new
  fields and enum values from newer servers pass through instead of raising.
- **Every tag is generated.** All of them cost about 3 ms more import time than a subset;
  the models dominate. GET endpoints returning images, streams or files become URL
  builders (`get_trickplay_tile_image_url(...)`, `get_item_image_url(...)`) with every
  query parameter from the spec. HEAD variants and uploads with image or text bodies are
  skipped, and the generator lists them.
- **Uploads are generated too.** Image uploads are sent base64-encoded: the spec declares
  them binary, but the server decodes them as base64 (`GetFromBase64Stream` in
  `ImageController`). Lyrics and client logs are sent as raw text.
- **Streaming downloads.** `client.stream(url)` yields the body in chunks and closes the
  connection when the block exits; `client.fetch_bytes(url)` is for small files. Both send
  the token in the header and refuse URLs of other hosts. Writing to a file is left to
  the caller, so the async stack never blocks the event loop on disk I/O.
- **Flat generated methods, namespaced helpers.** The generated endpoint methods sit
  directly on the client; hand-written helpers get a namespace, starting with
  `client.remote` (pause, seek, volume, mute, …).
- **401 and 403 are different errors.** `JellyfinAuthError` means the login is no longer
  valid; `JellyfinForbiddenError` means the user lacks permission (e.g. an admin-only
  request). Callers like Home Assistant must not start a reauth on the latter.
- **Only the async code is written by hand.** The WebSocket runs a single receive loop:
  KeepAlive is driven by the receive timeout, without a helper task. So unasync can turn
  it into the sync version unchanged. `stop()` works from any task or thread.
- **The caller owns the HTTP session or client**, so Home Assistant can pass its shared
  aiohttp session.
- **Header-based auth.** `Authorization: MediaBrowser …` for requests and the WebSocket;
  the `ApiKey` query parameter only on URLs handed to other players (`api_key` is off by
  default from Jellyfin 12).
- **The WebSocket is a hint channel.** `on_connect` runs after every reconnect so callers
  load or resync state over REST; a rejected token stops the loop instead of retrying.
- **No tokens in logs.** `websockets` logs request headers at debug level; the httpx
  adapter gives it a logger that masks the token.
- **No imports inside the event loop.** httpcore loads anyio's asyncio backend on the
  first request; the httpx adapter imports it up front, as Home Assistant's bootstrap does.

## Checked against a real server (Jellyfin 12.3.0)

- All three combinations (aiohttp async, httpx async, httpx sync) work: address probing,
  login, item queries and not-found mapping. The WebSocket held for 70 s, past the 60 s
  server timeout, and `stop()` returned within 0.25 s.
- REST responses fetched (system info, users, sessions, media folders, items with extra
  fields, item details, latest, resume, search hints) match the 12.4 spec: no unexpected
  nulls, unknown fields or enum values.
- The device name `Wohnzimmer, "TV" & Küche` is stored correctly whether percent-encoded or
  raw. Encoding stays, as it matches the TypeScript SDK.
- Login with username and password, `/Users/Me`, a wrong password (`JellyfinAuthError`)
  and logout (the token is invalid afterwards) all work.
- `ApiKey` works on URLs; `api_key` is rejected with 401.
- Unknown items give `JellyfinNotFoundError` when `userId` is passed; with an API key and
  no `userId` the server answers 400 instead.
- Remote control of a playing Jellyfin Web session works: pause, unpause, seek, volume,
  mute and unmute.
- `SessionsStart` does not push the current sessions; the server sends `Sessions` only on
  changes, so the initial state has to come from REST.

## Checks

```sh
python -m pytest   # every test runs against all three stack/adapter combinations
mypy
ruff check .
```

## License

MIT, see [LICENSE](LICENSE).
