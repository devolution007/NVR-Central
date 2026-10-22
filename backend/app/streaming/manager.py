"""Live stream sessions: authorise, enforce limits, configure MediaMTX, track viewers."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.integrations.adapter_factory import build_params
from app.integrations.hostguard import HostNotAllowedError, assert_host_allowed
from app.models.camera import Camera, CameraStreamProfile
from app.models.enums import CameraStatus, ConnectionType, NvrStatus, StreamKind, StreamSessionStatus
from app.models.nvr import Nvr
from app.models.streaming import StreamSession
from app.models.user import User
from app.streaming import mediamtx, paths


@dataclass(frozen=True)
class LiveInfo:
    can_stream: bool
    reason: str | None
    transcode: bool
    codec: str | None
    nvr_active_streams: int
    nvr_limit: int


def _profile_codec(db: Session, camera_id: uuid.UUID, stream: StreamKind) -> str | None:
    return db.scalar(
        select(CameraStreamProfile.codec).where(
            CameraStreamProfile.camera_id == camera_id, CameraStreamProfile.stream == stream
        )
    )


def active_sessions_for_nvr(db: Session, nvr_id: uuid.UUID) -> int:
    """Distinct (camera, stream, delivery) combinations being watched. Each is a separate upstream RTSP session on the
    NVR; several viewers of the same one share it, so they count once. (A converted and a native viewer of the same
    stream are two upstream sessions.)"""
    now = utcnow()
    sub = (
        select(StreamSession.camera_id, StreamSession.stream, StreamSession.transcoded)
        .join(Camera, Camera.id == StreamSession.camera_id)
        .where(
            Camera.nvr_id == nvr_id,
            StreamSession.status == StreamSessionStatus.ACTIVE,
            StreamSession.expires_at > now,
        )
        .distinct()
        .subquery()
    )
    return db.scalar(select(func.count()).select_from(sub)) or 0


def _blocked_reason(nvr: Nvr, camera: Camera) -> str | None:
    if nvr.connection_type == ConnectionType.P2P or not nvr.host:
        return "P2P NVRs cannot stream: direct P2P is disabled. Register this NVR with a tunnel or LAN address."
    if not nvr.enabled:
        return "This NVR is disabled."
    if nvr.status == NvrStatus.AUTH_FAILED:
        # Streaming with rejected credentials would add failed logins toward the NVR's account lockout.
        return "The NVR rejected its credentials. Update them before streaming."
    if nvr.last_success_at is None:
        return "This NVR has never connected successfully. Run Test connection first."
    if camera.status == CameraStatus.UNAVAILABLE:
        return "This camera is no longer reported by the NVR."
    return None


def nvr_stream_limit(nvr: Nvr) -> int:
    """This NVR's own limit if an admin set one, otherwise the server-wide default."""
    return nvr.max_live_streams or get_settings().max_streams_per_nvr


def live_info(db: Session, camera: Camera, nvr: Nvr, stream: StreamKind, client_hevc: bool = False) -> LiveInfo:
    s = get_settings()
    codec = _profile_codec(db, camera.id, stream)
    return LiveInfo(
        can_stream=_blocked_reason(nvr, camera) is None,
        reason=_blocked_reason(nvr, camera),
        transcode=paths.needs_transcode(codec, s.stream_transcode, client_hevc),
        codec=codec,
        nvr_active_streams=active_sessions_for_nvr(db, nvr.id),
        nvr_limit=nvr_stream_limit(nvr),
    )


def create_session(
    db: Session, user: User, camera: Camera, nvr: Nvr, stream: StreamKind, ip: str | None, client_hevc: bool = False
) -> StreamSession:
    s = get_settings()
    reason = _blocked_reason(nvr, camera)
    if reason:
        raise ApiError(409, "stream_not_available", reason)

    # Serialise concurrent starts for one NVR so the limit cannot be overshot by a race.
    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": nvr.id.int & 0x7FFFFFFFFFFFFFFF})
    now = utcnow()

    codec = _profile_codec(db, camera.id, stream)
    transcode = paths.needs_transcode(codec, s.stream_transcode, client_hevc)
    already_live = db.scalar(
        select(func.count())
        .select_from(StreamSession)
        .where(
            StreamSession.camera_id == camera.id,
            StreamSession.stream == stream,
            StreamSession.transcoded == transcode,
            StreamSession.status == StreamSessionStatus.ACTIVE,
            StreamSession.expires_at > now,
        )
    )
    if not already_live:  # joining a stream someone else is already watching costs the NVR nothing extra
        limit = nvr_stream_limit(nvr)
        if active_sessions_for_nvr(db, nvr.id) >= limit:
            raise ApiError(
                429, "nvr_stream_limit",
                f"This NVR already has {limit} live streams, its configured limit. "
                "Close one, or an admin can raise \"Max live streams\" in this NVR's settings "
                "after testing what it can take.",
            )  # fmt: skip
        total = (
            db.scalar(
                select(func.count())
                .select_from(StreamSession)
                .where(StreamSession.status == StreamSessionStatus.ACTIVE, StreamSession.expires_at > now)
            )
            or 0
        )
        if total >= s.max_streams_total:
            raise ApiError(429, "stream_limit", f"The server is at its limit of {s.max_streams_total} live streams.")

    try:
        assert_host_allowed(nvr.host or "")
    except HostNotAllowedError as exc:
        raise ApiError(409, "stream_not_available", exc.message) from exc

    params = build_params(nvr)
    source = paths.rtsp_source_url(params, camera.channel_number, stream)
    live = paths.path_name(paths.serve_kind(codec, transcode), camera.id, stream)
    try:
        if transcode:
            raw = paths.path_name("raw", camera.id, stream)
            mediamtx.upsert_path(raw, paths.source_conf(source))
            mediamtx.upsert_path(live, paths.transcode_conf(raw, stream))
        else:
            mediamtx.upsert_path(live, paths.source_conf(source))
    except mediamtx.StreamingUnavailableError as exc:
        raise ApiError(503, exc.code, exc.message) from exc
    except mediamtx.StreamingError as exc:
        raise ApiError(502, exc.code, exc.message) from exc

    session = StreamSession(
        camera_id=camera.id, user_id=user.id, stream=stream, path_name=live, transcoded=transcode,
        expires_at=now + timedelta(seconds=s.stream_session_ttl_seconds), ip_address=ip,
    )  # fmt: skip
    db.add(session)
    db.commit()
    return session


def keepalive(db: Session, session: StreamSession) -> StreamSession:
    session.expires_at = utcnow() + timedelta(seconds=get_settings().stream_session_ttl_seconds)
    db.commit()
    return session


def end_session(db: Session, session: StreamSession) -> None:
    if session.status == StreamSessionStatus.ACTIVE:
        session.status, session.ended_at = StreamSessionStatus.ENDED, utcnow()
        db.commit()


def sweep_expired(db: Session) -> int:
    """Mark sessions whose viewer vanished as expired. MediaMTX closes the NVR connection by itself once nobody reads
    the path, so no path cleanup is needed here."""
    now = utcnow()
    result = db.execute(
        update(StreamSession)
        .where(StreamSession.status == StreamSessionStatus.ACTIVE, StreamSession.expires_at <= now)
        .values(status=StreamSessionStatus.EXPIRED, ended_at=now)
    )
    db.commit()
    return int(result.rowcount)  # type: ignore[attr-defined]


def drop_nvr_paths(db: Session, nvr_id: uuid.UUID) -> None:
    """After an NVR's address or credentials change, or it is deleted: end its sessions and remove its paths so stale
    credentials do not linger in the gateway. Best effort: the gateway being down must not block the edit."""
    cams = list(db.scalars(select(Camera).where(Camera.nvr_id == nvr_id)))
    now = utcnow()
    db.execute(
        update(StreamSession)
        .where(StreamSession.camera_id.in_([c.id for c in cams]), StreamSession.status == StreamSessionStatus.ACTIVE)
        .values(status=StreamSessionStatus.ENDED, ended_at=now)
    )
    db.commit()
    for c in cams:
        for stream in StreamKind:
            for kind in ("live", "nat", "raw"):
                try:
                    mediamtx.delete_path(paths.path_name(kind, c.id, stream))
                except mediamtx.StreamingError:
                    return
