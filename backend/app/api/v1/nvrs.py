import uuid

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession, allowed_campus_ids, assert_campus_access
from app.api.v1.cameras import cameras_out
from app.core.errors import ApiError, conflict, not_found
from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.db.base import utcnow
from app.models.camera import Camera
from app.models.campus import Campus
from app.models.enums import CameraStatus, CapabilityName, CapabilityStatus, ConnectionType, NvrStatus
from app.models.nvr import Nvr, NvrCapability
from app.schemas.camera import CameraOut, CapabilityOut, OperationQueued
from app.schemas.common import Page
from app.schemas.nvr import NvrCreate, NvrOut, NvrUpdate
from app.services import audit, nvr_ops
from app.services.nvr_ops import Operation
from app.streaming import manager as streaming
from app.workers.dispatch import QueueUnavailableError, enqueue

router = APIRouter(prefix="/nvrs", tags=["nvrs"])


def _out(db: Session, nvr: Nvr) -> NvrOut:
    campus = db.get(Campus, nvr.campus_id)
    cams = (
        db.scalar(
            select(func.count())
            .select_from(Camera)
            .where(Camera.nvr_id == nvr.id, Camera.status != CameraStatus.UNAVAILABLE)
        )
        or 0
    )
    masked = mask_secret(decrypt_secret(nvr.p2p_serial_encrypted)) if nvr.p2p_serial_encrypted else None
    return NvrOut(
        id=nvr.id,
        campus_id=nvr.campus_id,
        campus_name=campus.name if campus else "",
        name=nvr.name,
        connection_type=nvr.connection_type,
        host=nvr.host,
        http_port=nvr.http_port,
        rtsp_port=nvr.rtsp_port,
        username=nvr.username,
        has_password=bool(nvr.password_encrypted),
        p2p_serial_masked=masked,
        status=nvr.status,
        status_message=nvr.status_message,
        model=nvr.model,
        firmware=nvr.firmware,
        enabled=nvr.enabled,
        max_live_streams=nvr.max_live_streams,
        effective_max_live_streams=streaming.nvr_stream_limit(nvr),
        camera_count=cams,
        utc_offset_minutes=nvr.utc_offset_minutes,
        last_checked_at=nvr.last_checked_at,
        last_success_at=nvr.last_success_at,
        created_at=nvr.created_at,
        updated_at=nvr.updated_at,
    )


def _get_visible(db: Session, user: CurrentUser, nvr_id: uuid.UUID) -> Nvr:
    nvr = db.get(Nvr, nvr_id)
    if nvr is None or nvr.deleted_at is not None:
        raise not_found("NVR")
    try:
        assert_campus_access(db, user, nvr.campus_id)
    except ApiError as exc:
        raise not_found("NVR") from exc
    return nvr


def _require_campus(db: Session, campus_id: uuid.UUID) -> Campus:
    campus = db.get(Campus, campus_id)
    if campus is None or campus.deleted_at is not None:
        raise ApiError(
            422, "validation_error", "Campus does not exist", [{"field": "campus_id", "message": "Unknown campus"}]
        )
    return campus


def _queue(db: Session, nvr: Nvr, op: Operation) -> None:
    """Claim the NVR's single in-flight slot and hand the work to a worker."""
    if not nvr.enabled:
        raise ApiError(409, "nvr_disabled", "Enable this NVR before testing it")
    previous = nvr_ops.claim(db, nvr.id)
    if previous is None:
        raise ApiError(409, "operation_in_progress", "Another check is already running for this NVR")
    try:
        enqueue(nvr.id, op)
    except QueueUnavailableError as exc:
        nvr_ops.restore(db, nvr.id, previous, "Not checked: the background worker queue is unavailable")
        raise ApiError(
            503, "worker_unavailable", "The background worker queue is unavailable. Is Redis running?"
        ) from exc


def _trigger(
    nvr_id: uuid.UUID, op: Operation, request: Request, user: AdminUser, db: Session, force: bool
) -> OperationQueued:
    nvr = _get_visible(db, user, nvr_id)
    if nvr.status == NvrStatus.AUTH_FAILED and not force:
        raise ApiError(
            409,
            "credentials_rejected",
            "The NVR rejected these credentials earlier. Update them first: "
            "repeated wrong attempts can lock the NVR account.",
        )
    _queue(db, nvr, op)
    audit.record(
        db,
        request,
        f"nvr.{op.value}",
        actor=user,
        entity_type="nvr",
        entity_id=nvr.id,
        campus_id=nvr.campus_id,
        details={"name": nvr.name, "force": force},
    )
    db.commit()
    return OperationQueued(nvr_id=nvr.id, operation=op.value)


@router.post("/{nvr_id}/test", response_model=OperationQueued, status_code=202)
def test_nvr(
    nvr_id: uuid.UUID, request: Request, user: AdminUser, db: DbSession, force: bool = False
) -> OperationQueued:
    """Connect, read device info and probe capabilities. Does not change the camera inventory."""
    return _trigger(nvr_id, Operation.TEST, request, user, db, force)


@router.post("/{nvr_id}/discover", response_model=OperationQueued, status_code=202)
def discover_nvr(
    nvr_id: uuid.UUID, request: Request, user: AdminUser, db: DbSession, force: bool = False
) -> OperationQueued:
    """Re-read the camera list and stream profiles. Existing cameras and backup policies are kept."""
    return _trigger(nvr_id, Operation.DISCOVER, request, user, db, force)


@router.post("/{nvr_id}/reconnect", response_model=OperationQueued, status_code=202)
def reconnect_nvr(
    nvr_id: uuid.UUID, request: Request, user: AdminUser, db: DbSession, force: bool = False
) -> OperationQueued:
    """Full re-check after a change or outage: connection test plus camera discovery."""
    return _trigger(nvr_id, Operation.RECONNECT, request, user, db, force)


@router.get("/{nvr_id}/capabilities", response_model=list[CapabilityOut])
def nvr_capabilities(nvr_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[CapabilityOut]:
    nvr = _get_visible(db, user, nvr_id)
    rows = {c.name: c for c in db.scalars(select(NvrCapability).where(NvrCapability.nvr_id == nvr.id))}
    return [
        CapabilityOut(name=n, status=r.status, detail=r.detail, checked_at=r.checked_at)
        if (r := rows.get(n))
        else CapabilityOut(name=n, status=CapabilityStatus.UNKNOWN, detail="Not checked yet", checked_at=None)
        for n in CapabilityName
    ]


@router.get("/{nvr_id}/cameras", response_model=list[CameraOut])
def nvr_cameras(nvr_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[CameraOut]:
    nvr = _get_visible(db, user, nvr_id)
    rows = list(db.scalars(select(Camera).where(Camera.nvr_id == nvr.id).order_by(Camera.channel_number)))
    return cameras_out(db, rows)


@router.get("", response_model=Page[NvrOut])
def list_nvrs(
    user: CurrentUser,
    db: DbSession,
    campus_id: uuid.UUID | None = None,
    status: NvrStatus | None = None,
    q: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[NvrOut]:
    stmt = select(Nvr).where(Nvr.deleted_at.is_(None))
    allowed = allowed_campus_ids(db, user)
    if allowed is not None:
        stmt = stmt.where(Nvr.campus_id.in_(allowed))
    if campus_id:
        stmt = stmt.where(Nvr.campus_id == campus_id)
    if status:
        stmt = stmt.where(Nvr.status == status)
    if q:
        stmt = stmt.where(Nvr.name.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(func.lower(Nvr.name)).offset((page - 1) * page_size).limit(page_size))
    return Page(items=[_out(db, n) for n in rows], total=total, page=page, page_size=page_size)


@router.post("", response_model=NvrOut, status_code=201)
def create_nvr(payload: NvrCreate, request: Request, user: AdminUser, db: DbSession) -> NvrOut:
    _require_campus(db, payload.campus_id)
    nvr = Nvr(
        campus_id=payload.campus_id,
        name=payload.name,
        connection_type=payload.connection_type,
        host=payload.host,
        http_port=payload.http_port,
        rtsp_port=payload.rtsp_port,
        username=payload.username,
        password_encrypted=encrypt_secret(payload.password),
        p2p_serial_encrypted=encrypt_secret(payload.p2p_serial) if payload.p2p_serial else None,
        enabled=payload.enabled,
        max_live_streams=payload.max_live_streams,
        status=NvrStatus.PENDING,
        status_changed_at=utcnow(),
    )
    db.add(nvr)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("An NVR with this name already exists in the campus", "nvr_name_taken") from exc
    audit.record(
        db,
        request,
        "nvr.create",
        actor=user,
        entity_type="nvr",
        entity_id=nvr.id,
        campus_id=nvr.campus_id,
        details={"name": nvr.name, "connection_type": nvr.connection_type.value},
    )
    db.commit()
    if payload.auto_connect and nvr.enabled:
        try:
            _queue(db, nvr, Operation.RECONNECT)
        except ApiError as exc:
            # Registration itself succeeded; tell the user why nothing was queued instead of failing the save.
            nvr_ops.restore(db, nvr.id, NvrStatus.PENDING, exc.message)
        db.refresh(nvr)
    return _out(db, nvr)


@router.get("/{nvr_id}", response_model=NvrOut)
def get_nvr(nvr_id: uuid.UUID, user: CurrentUser, db: DbSession) -> NvrOut:
    return _out(db, _get_visible(db, user, nvr_id))


@router.put("/{nvr_id}", response_model=NvrOut)
def update_nvr(nvr_id: uuid.UUID, payload: NvrUpdate, request: Request, user: AdminUser, db: DbSession) -> NvrOut:
    nvr = _get_visible(db, user, nvr_id)
    _require_campus(db, payload.campus_id)

    if payload.connection_type in (ConnectionType.TUNNEL, ConnectionType.LAN) and not payload.host:
        raise ApiError(
            422,
            "validation_error",
            "host is required for tunnel and lan connections",
            [{"field": "host", "message": "Required"}],
        )
    if payload.connection_type == ConnectionType.P2P and not (payload.p2p_serial or nvr.p2p_serial_encrypted):
        raise ApiError(
            422,
            "validation_error",
            "p2p_serial is required for p2p connections",
            [{"field": "p2p_serial", "message": "Required"}],
        )

    connection_changed = (
        (payload.connection_type, payload.host, payload.http_port, payload.rtsp_port, payload.username)
        != (nvr.connection_type, nvr.host, nvr.http_port, nvr.rtsp_port, nvr.username)
        or payload.password is not None
        or payload.p2p_serial is not None
    )

    nvr.campus_id, nvr.name, nvr.connection_type = payload.campus_id, payload.name, payload.connection_type
    nvr.host, nvr.http_port, nvr.rtsp_port = payload.host, payload.http_port, payload.rtsp_port
    nvr.username, nvr.enabled = payload.username, payload.enabled
    nvr.max_live_streams = payload.max_live_streams  # a limit change never needs a reconnect
    if payload.password is not None:
        nvr.password_encrypted = encrypt_secret(payload.password)
    if payload.p2p_serial is not None:
        nvr.p2p_serial_encrypted = encrypt_secret(payload.p2p_serial)
    if connection_changed:
        # Old status no longer describes this connection profile.
        nvr.status, nvr.status_message, nvr.status_changed_at = NvrStatus.PENDING, None, utcnow()
    nvr.updated_at = utcnow()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("An NVR with this name already exists in the campus", "nvr_name_taken") from exc
    audit.record(
        db,
        request,
        "nvr.update",
        actor=user,
        entity_type="nvr",
        entity_id=nvr.id,
        campus_id=nvr.campus_id,
        details={
            "name": nvr.name,
            "credentials_changed": payload.password is not None or payload.p2p_serial is not None,
        },
    )
    db.commit()
    if connection_changed or not nvr.enabled:
        streaming.drop_nvr_paths(db, nvr.id)  # stale credentials must not linger in the live-view gateway
    return _out(db, nvr)


@router.delete("/{nvr_id}", status_code=204)
def delete_nvr(nvr_id: uuid.UUID, request: Request, user: AdminUser, db: DbSession) -> None:
    nvr = _get_visible(db, user, nvr_id)
    # Soft delete: cameras and backup history stay so past recordings remain traceable.
    nvr.deleted_at = utcnow()
    audit.record(
        db,
        request,
        "nvr.delete",
        actor=user,
        entity_type="nvr",
        entity_id=nvr.id,
        campus_id=nvr.campus_id,
        details={"name": nvr.name},
    )
    db.commit()
    streaming.drop_nvr_paths(db, nvr.id)
