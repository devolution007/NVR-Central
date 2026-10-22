import re
import uuid
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.api.deps import AdminUser, CurrentUser, DbSession, allowed_campus_ids
from app.core.config import get_settings
from app.core.errors import ApiError, not_found
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


def _forward_params(request: Request) -> dict[str, str]:
    """MediaMTX tags every playlist and segment URL with `?session=<uuid>` to find the viewer's muxer session. That is
    the only client parameter passed on, and only if it is a well-formed UUID; everything else is dropped."""
    params = {"cookieCheck": "1"}
    session = request.query_params.get("session", "")
    if UUID_RE.match(session):
        params["session"] = session
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


def _live_path(session_id: uuid.UUID, user: CurrentUser, db: DbSession) -> str:
    sess = _session_for_user(db, user, session_id, owner_only=True)
    _require_live(db, user, sess)
    return sess.path_name


@router.get("/{session_id}/hls/{file:path}")
async def hls_proxy(request: Request, file: str, path_name: str = Depends(_live_path)) -> StreamingResponse:
    """Serve HLS for one authorised session. The browser only ever sees this URL; MediaMTX is never exposed."""
    if not HLS_FILE.match(file) or not file.endswith(HLS_SUFFIXES):
        raise not_found("Stream file")
    # MediaMTX answers a first HLS request with a 302 "cookie check" redirect (observed on v1.21.2, not documented in
    # its config). Sending its own marker parameter up front skips that extra round trip; redirects are still followed.
    client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=3.0), follow_redirects=True)
    try:
        upstream = await client.send(
            client.build_request(
                "GET", f"{get_settings().mediamtx_hls_url}/{path_name}/{file}", params=_forward_params(request)
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise ApiError(503, "streaming_unavailable", "The live-view gateway is not reachable.") from exc
    if upstream.status_code != 200:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
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
        await client.aclose()

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
