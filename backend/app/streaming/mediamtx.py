"""Thin client for MediaMTX's control API (verified against its published openapi.yaml, v1.21.x):
  POST   /v3/config/paths/add/{name}      POST /v3/config/paths/replace/{name}
  DELETE /v3/config/paths/delete/{name}   GET  /v3/paths/list
Path configuration lives in MediaMTX memory only, so the backend re-applies it every time a session starts. That also
means NVR credential changes take effect on the next session. Nothing here is returned to browsers."""

from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings


class StreamingError(Exception):
    def __init__(self, message: str, code: str = "streaming_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class StreamingUnavailableError(StreamingError):
    def __init__(self) -> None:
        super().__init__("The live-view gateway (MediaMTX) is not reachable. Is it running?", "streaming_unavailable")


def _client() -> httpx.Client:
    return httpx.Client(base_url=get_settings().mediamtx_api_url, timeout=httpx.Timeout(5.0, connect=2.0))


def _name(name: str) -> str:
    return quote(name, safe="")


def upsert_path(name: str, conf: dict[str, Any]) -> None:
    """Create the path, or replace its configuration if it already exists (idempotent)."""
    try:
        with _client() as c:
            r = c.post(f"/v3/config/paths/replace/{_name(name)}", json=conf)
            if r.status_code == 404:
                r = c.post(f"/v3/config/paths/add/{_name(name)}", json=conf)
    except httpx.HTTPError as exc:
        raise StreamingUnavailableError() from exc
    if r.status_code != 200:
        # The response body can echo the configuration (which contains NVR credentials), so it is never surfaced.
        raise StreamingError(f"The live-view gateway rejected the stream configuration (HTTP {r.status_code})")


def delete_path(name: str) -> None:
    try:
        with _client() as c:
            r = c.delete(f"/v3/config/paths/delete/{_name(name)}")
    except httpx.HTTPError as exc:
        raise StreamingUnavailableError() from exc
    if r.status_code not in (200, 404):
        raise StreamingError(f"Could not remove the stream path (HTTP {r.status_code})")


def list_paths() -> list[dict[str, Any]]:
    """Runtime state of all paths (readers, availability). Used by the admin overview only."""
    try:
        with _client() as c:
            r = c.get("/v3/paths/list", params={"itemsPerPage": 1000})
    except httpx.HTTPError as exc:
        raise StreamingUnavailableError() from exc
    if r.status_code != 200:
        raise StreamingError(f"Could not read stream state (HTTP {r.status_code})")
    items: list[dict[str, Any]] = r.json().get("items", [])
    return items


def is_reachable() -> bool:
    try:
        with _client() as c:
            return c.get("/v3/paths/list", params={"itemsPerPage": 1}).status_code == 200
    except httpx.HTTPError:
        return False
