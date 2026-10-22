import uuid

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, allowed_campus_ids
from app.core.config import get_settings
from app.core.errors import not_found
from app.models.camera import Camera, CameraStreamProfile
from app.models.campus import Campus
from app.models.enums import CameraStatus, StreamKind
from app.models.nvr import Nvr
from app.models.streaming import CameraFavorite
from app.models.user import User
from app.schemas.camera import CameraOut, CameraStatusOut, StreamProfileOut
from app.schemas.common import Page
from app.schemas.streaming import LiveInfoOut, StreamSessionOut, StreamStart
from app.services.audit import client_ip
from app.streaming import manager

router = APIRouter(prefix="/cameras", tags=["cameras"])


def cameras_out(db: Session, cams: list[Camera], user: User | None = None) -> list[CameraOut]:
    """Build responses for many cameras with a handful of queries instead of several per camera."""
    if not cams:
        return []
    ids = [c.id for c in cams]
    favorites: set[uuid.UUID] = set()
    if user is not None:
        favorites = set(
            db.scalars(
                select(CameraFavorite.camera_id).where(
                    CameraFavorite.user_id == user.id, CameraFavorite.camera_id.in_(ids)
                )
            )
        )
    profiles: dict[tuple[uuid.UUID, StreamKind], StreamProfileOut] = {}
    for p in db.scalars(select(CameraStreamProfile).where(CameraStreamProfile.camera_id.in_(ids))):
        profiles[(p.camera_id, p.stream)] = StreamProfileOut(
            stream=p.stream, codec=p.codec, width=p.width, height=p.height, bitrate_kbps=p.bitrate_kbps, fps=p.fps
        )
    nvr_names = {n.id: n.name for n in db.scalars(select(Nvr).where(Nvr.id.in_({c.nvr_id for c in cams})))}
    campus_names = {c.id: c.name for c in db.scalars(select(Campus).where(Campus.id.in_({c.campus_id for c in cams})))}
    return [
        CameraOut(
            id=c.id,
            nvr_id=c.nvr_id,
            nvr_name=nvr_names.get(c.nvr_id, ""),
            campus_id=c.campus_id,
            campus_name=campus_names.get(c.campus_id, ""),
            channel_number=c.channel_number,
            name=c.name,
            model=c.model,
            status=c.status,
            status_detail=c.status_detail,
            status_checked_at=c.status_checked_at,
            last_discovered_at=c.last_discovered_at,
            main_stream=profiles.get((c.id, StreamKind.MAIN)),
            sub_stream=profiles.get((c.id, StreamKind.SUB)),
            is_favorite=c.id in favorites,
        )
        for c in cams
    ]


def _visible_camera(db: Session, user: CurrentUser, camera_id: uuid.UUID) -> Camera:
    cam = db.get(Camera, camera_id)
    allowed = allowed_campus_ids(db, user)
    nvr = db.get(Nvr, cam.nvr_id) if cam else None
    if (
        cam is None
        or nvr is None
        or nvr.deleted_at is not None
        or (allowed is not None and cam.campus_id not in allowed)
    ):
        raise not_found("Camera")
    return cam


@router.get("", response_model=Page[CameraOut])
def list_cameras(
    user: CurrentUser,
    db: DbSession,
    campus_id: uuid.UUID | None = None,
    nvr_id: uuid.UUID | None = None,
    status: CameraStatus | None = None,
    q: str | None = Query(default=None, max_length=128),
    ids: list[uuid.UUID] | None = Query(default=None, max_length=100),
    favorites: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> Page[CameraOut]:
    stmt = select(Camera).join(Nvr, Nvr.id == Camera.nvr_id).where(Nvr.deleted_at.is_(None))
    allowed = allowed_campus_ids(db, user)
    if allowed is not None:
        stmt = stmt.where(Camera.campus_id.in_(allowed))
    if ids:
        stmt = stmt.where(Camera.id.in_(ids))
    if favorites:
        stmt = stmt.where(Camera.id.in_(select(CameraFavorite.camera_id).where(CameraFavorite.user_id == user.id)))
    if campus_id:
        stmt = stmt.where(Camera.campus_id == campus_id)
    if nvr_id:
        stmt = stmt.where(Camera.nvr_id == nvr_id)
    if status:
        stmt = stmt.where(Camera.status == status)
    if q:
        stmt = stmt.where(Camera.name.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(Camera.campus_id, Camera.nvr_id, Camera.channel_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(items=cameras_out(db, rows, user), total=total, page=page, page_size=page_size)


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CameraOut:
    return cameras_out(db, [_visible_camera(db, user, camera_id)], user)[0]


@router.put("/{camera_id}/favorite", status_code=204)
def add_favorite(camera_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    cam = _visible_camera(db, user, camera_id)
    if db.get(CameraFavorite, (user.id, cam.id)) is None:
        db.add(CameraFavorite(user_id=user.id, camera_id=cam.id))
        db.commit()


@router.delete("/{camera_id}/favorite", status_code=204)
def remove_favorite(camera_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    fav = db.get(CameraFavorite, (user.id, camera_id))
    if fav is not None:
        db.delete(fav)
        db.commit()


@router.get("/{camera_id}/live", response_model=LiveInfoOut)
def camera_live_info(
    camera_id: uuid.UUID, user: CurrentUser, db: DbSession, stream: StreamKind = StreamKind.SUB, hevc: bool = False
) -> LiveInfoOut:
    cam = _visible_camera(db, user, camera_id)
    nvr = db.get(Nvr, cam.nvr_id)
    assert nvr is not None
    info = manager.live_info(db, cam, nvr, stream, hevc)
    return LiveInfoOut(
        can_stream=info.can_stream, reason=info.reason, stream=stream, codec=info.codec, transcode=info.transcode,
        nvr_active_streams=info.nvr_active_streams, nvr_limit=info.nvr_limit,
    )  # fmt: skip


@router.post("/{camera_id}/stream-session", response_model=StreamSessionOut, status_code=201)
def start_stream(
    camera_id: uuid.UUID, payload: StreamStart, request: Request, user: CurrentUser, db: DbSession
) -> StreamSessionOut:
    cam = _visible_camera(db, user, camera_id)
    nvr = db.get(Nvr, cam.nvr_id)
    assert nvr is not None
    sess = manager.create_session(db, user, cam, nvr, payload.stream, client_ip(request), payload.native_hevc)
    ttl = get_settings().stream_session_ttl_seconds
    return StreamSessionOut(
        id=sess.id, camera_id=cam.id, stream=sess.stream, hls_url=f"/api/v1/stream-sessions/{sess.id}/hls/index.m3u8",
        transcoded=sess.transcoded, expires_at=sess.expires_at, keepalive_every_seconds=max(5, ttl // 3),
    )  # fmt: skip


@router.get("/{camera_id}/status", response_model=CameraStatusOut)
def get_camera_status(camera_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CameraStatusOut:
    cam = _visible_camera(db, user, camera_id)
    return CameraStatusOut(status=cam.status, detail=cam.status_detail, checked_at=cam.status_checked_at)
