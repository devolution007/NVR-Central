import re
import time
import uuid
from collections.abc import AsyncIterator
from http.cookiejar import DefaultCookiePolicy

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.deps import ACCESS_COOKIE, AdminUser, CurrentUser, DbSession, allowed_campus_ids, get_current_user
from app.core.config import get_settings
from app.core.errors import ApiError, not_found
from app.core.security import decode_access_token
from app.db.base import utcnow
from app.models.camera import Camera
from app.models.campus import Campus
from app.models.enums import Role, StreamSessionStatus
from app.models.nvr import Nvr
from app.models.streaming import StreamSession
from app.models.user import User
from app.schemas.streaming import ActiveStreamOut, StreamingStatusOut
from app.streaming import manager, mediamtx

router = APIRouter(prefix="/stream-sessions", tags=["streaming"])
gateway_router = APIRouter(prefix="/streaming", tags=["streaming"])

# Only plain HLS file names (playlist, init segment, media segments) in a flat or one-level layout. No "..", no query.
HLS_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*(/[A-Za-z0-9_][A-Za-z0-9_.\-]*)?$")
HLS_SUFFIXES = (".m3u8", ".mp4", ".m4s", ".ts", ".aac")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


_client: httpx.AsyncClient | None = None


def _gateway_client() -> httpx.AsyncClient:
    """One shared client for all HLS requests. Building a client per request (it loads TLS certificates) cost more CPU
    than the proxying itself once low-latency HLS made it ~100 requests a second. Cookies are never kept, so viewers
    cannot share gateway state through it; each request carries its own `session` parameter."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=3.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
        _client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return _client


def _forward_params(request: Request) -> dict[str, str]:
    """MediaMTX tags every playlist and segment URL with `?session=<uuid>` to find the viewer's muxer session. That is
    the only client parameter passed on, and only if it is a well-formed UUID; everything else is dropped."""
    params = {"cookieCheck": "1"}
    session = request.query_params.get("session", "")
    if UUID_RE.match(session):
        params["session"] = session
    # Low-latency HLS: blocking playlist reload asks for a media sequence / part number (digits only).
    for key in ("_HLS_msn", "_HLS_part"):
        value = request.query_params.get(key, "")
        if value.isascii() and value.isdigit() and len(value) <= 9:
            params[key] = value
    if request.query_params.get("_HLS_skip") == "YES":
        params["_HLS_skip"] = "YES"
    return params


def _session_for_user(db: DbSession, user: User, session_id: uuid.UUID, *, owner_only: bool) -> StreamSession:
    sess = db.get(StreamSession, session_id)
    if sess is None or (owner_only or user.role != Role.ADMIN) and sess.user_id != user.id:
        raise not_found("Stream session")
    return sess


def _require_live(db: DbSession, user: User, sess: StreamSession) -> None:
    """The session must be active and unexpired, and the viewer must still be allowed to see the camera's campus
    (their access may have been revoked since the session started)."""
    if sess.status != StreamSessionStatus.ACTIVE or sess.expires_at <= utcnow():
        raise ApiError(410, "stream_session_expired", "This stream session has ended. Start a new one.")
    allowed = allowed_campus_ids(db, user)
    if allowed is not None:
        cam = db.get(Camera, sess.camera_id)
        if cam is None or cam.campus_id not in allowed:
            raise not_found("Stream session")


@router.post("/{session_id}/keepalive")
def session_keepalive(session_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, object]:
    sess = _session_for_user(db, user, session_id, owner_only=True)
    _require_live(db, user, sess)
    manager.keepalive(db, sess)
    return {"expires_at": sess.expires_at}


@router.delete("/{session_id}", status_code=204)
def stop_session(session_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    sess = _session_for_user(db, user, session_id, owner_only=False)
    manager.end_session(db, sess)


def _authorise_hls(request: Request, db: Session, session_id: uuid.UUID) -> str:
    """Full check: user still active, session theirs and live, campus still visible. Runs in a worker thread.
    The connection goes back to the pool before returning: a proxied request can wait seconds on the gateway."""
    try:
        user = get_current_user(request, db)
        sess = _session_for_user(db, user, session_id, owner_only=True)
        _require_live(db, user, sess)
        return sess.path_name
    finally:
        db.close()


# Low-latency HLS makes each viewer fetch ~4 playlists and parts a second. Re-reading the user, session and campus
# permissions from the database for each one was the main CPU cost, so a positive result is reused for a few seconds.
# The access token itself is verified on every request; a disabled user or ended session loses access within the TTL.
_HLS_AUTH_TTL = 3.0
_hls_auth: dict[tuple[uuid.UUID, uuid.UUID], tuple[float, str]] = {}


async def _hls_path(request: Request, session_id: uuid.UUID, db: DbSession) -> str:
    token = request.cookies.get(ACCESS_COOKIE)
    if token is None and request.headers.get("authorization", "").lower().startswith("bearer "):
        token = request.headers["authorization"][7:].strip()
    payload = decode_access_token(token) if token else None
    if payload is None:
        raise ApiError(401, "invalid_token", "Session expired or invalid")
    try:
        key = (uuid.UUID(payload["sub"]), session_id)
    except (KeyError, ValueError) as exc:
        raise ApiError(401, "invalid_token", "Session expired or invalid") from exc
    now = time.monotonic()
    hit = _hls_auth.get(key)
    if hit and hit[0] > now:
        return hit[1]
    path_name = await run_in_threadpool(_authorise_hls, request, db, session_id)
    if len(_hls_auth) > 2000:
        _hls_auth.clear()
    _hls_auth[key] = (now + _HLS_AUTH_TTL, path_name)
    return path_name


@router.get("/{session_id}/hls/{file:path}")
async def hls_proxy(request: Request, file: str, path_name: str = Depends(_hls_path)) -> StreamingResponse:
    """Serve HLS for one authorised session. The browser only ever sees this URL; MediaMTX is never exposed."""
    if not HLS_FILE.match(file) or not file.endswith(HLS_SUFFIXES):
        raise not_found("Stream file")
    # MediaMTX answers a first HLS request with a 302 "cookie check" redirect (observed on v1.21.2, not documented in
    # its config). Sending its own marker parameter up front skips that extra round trip; redirects are still followed.
    client = _gateway_client()
    try:
        upstream = await client.send(
            client.build_request(
                "GET", f"{get_settings().mediamtx_hls_url}/{path_name}/{file}", params=_forward_params(request)
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        raise ApiError(503, "streaming_unavailable", "The live-view gateway is not reachable.") from exc
    if upstream.status_code != 200:
        status = upstream.status_code
        await upstream.aclose()
        if status == 404:
            raise ApiError(
                404, "stream_not_ready", "The stream is not available yet. The camera may be offline or still starting."
            )
        raise ApiError(502, "streaming_error", f"The live-view gateway returned HTTP {status}.")

    async def body() -> AsyncIterator[bytes]:
        async for chunk in upstream.aiter_raw():
            yield chunk

    async def close() -> None:
        await upstream.aclose()

    return StreamingResponse(
        body(),
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(close),
    )


@router.get("", response_model=list[ActiveStreamOut])
def active_streams(_: AdminUser, db: DbSession) -> list[ActiveStreamOut]:
    now = utcnow()
    rows = db.execute(
        select(StreamSession, Camera, Nvr, Campus, User)
        .join(Camera, Camera.id == StreamSession.camera_id)
        .join(Nvr, Nvr.id == Camera.nvr_id)
        .join(Campus, Campus.id == Camera.campus_id)
        .join(User, User.id == StreamSession.user_id)
        .where(StreamSession.status == StreamSessionStatus.ACTIVE, StreamSession.expires_at > now)
        .order_by(StreamSession.created_at.desc())
    )
    return [
        ActiveStreamOut(
            id=s.id, camera_id=c.id, camera_name=c.name, nvr_name=n.name, campus_name=ca.name, stream=s.stream,
            username=u.username, transcoded=s.transcoded, created_at=s.created_at, expires_at=s.expires_at,
        )
        for s, c, n, ca, u in rows
    ]  # fmt: skip


@gateway_router.get("/status", response_model=StreamingStatusOut)
def streaming_status(_: AdminUser, db: DbSession) -> StreamingStatusOut:
    s = get_settings()
    now = utcnow()
    active = (
        db.query(StreamSession)
        .filter(StreamSession.status == StreamSessionStatus.ACTIVE, StreamSession.expires_at > now)
        .count()
    )
    return StreamingStatusOut(
        gateway_reachable=mediamtx.is_reachable(), active_sessions=active, max_streams_per_nvr=s.max_streams_per_nvr,
        max_streams_total=s.max_streams_total, transcode_mode=s.stream_transcode,
    )  # fmt: skip
