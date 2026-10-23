import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Query, Request
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession, OperatorUser, allowed_campus_ids
from app.api.v1.cameras import _visible_camera
from app.core.config import get_settings
from app.core.errors import ApiError, conflict, not_found
from app.core.security import encrypt_secret
from app.db.base import utcnow
from app.integrations.adapter_factory import get_adapter
from app.integrations.base import NvrError
from app.integrations.hostguard import assert_host_allowed
from app.integrations.sftp import SftpError, SftpSession, fetch_host_key
from app.models.backup import BackupFile, BackupJob, SftpDestination
from app.models.camera import Camera, CameraBackupPolicy
from app.models.campus import Campus
from app.models.enums import (
    BackupFileStatus,
    BackupJobStatus,
    BackupStream,
    BackupTrigger,
    ConnectionType,
    NvrStatus,
    RecordingMode,
    SftpAuthType,
    StreamKind,
)
from app.models.nvr import Nvr
from app.models.user import User
from app.schemas.backup import (
    BackupStatusOut,
    DestinationCreate,
    DestinationOption,
    DestinationOut,
    DestinationUpdate,
    FileOut,
    HostKeyOut,
    HostKeyProbe,
    JobCreate,
    JobDetail,
    JobOut,
    PolicyBulk,
    PolicyFields,
    PolicyOut,
    PolicyUpdate,
    RecordingOut,
    RecordingSearchOut,
)
from app.schemas.common import Page
from app.services import audit, backup
from app.workers.dispatch import QueueUnavailableError, enqueue_backup

router = APIRouter(prefix="/backups", tags=["backups"])

MAX_SEARCH_HOURS = 48
MAX_MANUAL_DAYS = 7


# ---------------------------------------------------------------------------------------------- destinations
def _dest_out(db: Session, d: SftpDestination) -> DestinationOut:
    policies = (
        db.scalar(select(func.count()).select_from(CameraBackupPolicy).where(CameraBackupPolicy.destination_id == d.id))
        or 0
    )
    return DestinationOut(
        id=d.id,
        name=d.name,
        host=d.host,
        port=d.port,
        username=d.username,
        auth_type=d.auth_type,
        has_secret=bool(d.password_encrypted or d.private_key_encrypted),
        base_path=d.base_path,
        host_key_type=d.host_key_type,
        host_key_sha256=d.host_key_sha256,
        enabled=d.enabled,
        last_test_at=d.last_test_at,
        last_test_ok=d.last_test_ok,
        last_test_message=d.last_test_message,
        policy_count=policies,
        created_at=d.created_at,
    )


def _get_dest(db: Session, dest_id: uuid.UUID) -> SftpDestination:
    d = db.get(SftpDestination, dest_id)
    if d is None or d.deleted_at is not None:
        raise not_found("Destination")
    return d


def _guard_host(host: str) -> None:
    try:
        assert_host_allowed(host)
    except NvrError as exc:  # HostNotAllowedError / unresolvable name
        raise ApiError(422, "host_not_allowed", exc.message, [{"field": "host", "message": exc.message}]) from exc


@router.get("/destinations", response_model=list[DestinationOut])
def list_destinations(_: AdminUser, db: DbSession) -> list[DestinationOut]:
    rows = db.scalars(
        select(SftpDestination).where(SftpDestination.deleted_at.is_(None)).order_by(SftpDestination.name)
    )
    return [_dest_out(db, d) for d in rows]


@router.get("/destinations/options", response_model=list[DestinationOption])
def destination_options(_: OperatorUser, db: DbSession) -> list[DestinationOption]:
    rows = db.scalars(
        select(SftpDestination).where(SftpDestination.deleted_at.is_(None)).order_by(SftpDestination.name)
    )
    return [DestinationOption(id=d.id, name=d.name, enabled=d.enabled) for d in rows]


@router.post("/destinations/host-key", response_model=HostKeyOut)
def probe_host_key(payload: HostKeyProbe, _: AdminUser) -> HostKeyOut:
    """Read the server's host key so the administrator can compare the fingerprint with the real server's before
    trusting it. Nothing is stored."""
    _guard_host(payload.host)
    try:
        info = fetch_host_key(payload.host, payload.port)
    except SftpError as exc:
        raise ApiError(502, "sftp_unreachable", exc.message) from exc
    return HostKeyOut(key_type=info.key_type, sha256=info.sha256)


@router.post("/destinations", response_model=DestinationOut, status_code=201)
def create_destination(payload: DestinationCreate, request: Request, user: AdminUser, db: DbSession) -> DestinationOut:
    _guard_host(payload.host)
    d = SftpDestination(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        auth_type=payload.auth_type,
        password_encrypted=encrypt_secret(payload.password) if payload.password else None,
        private_key_encrypted=encrypt_secret(payload.private_key) if payload.private_key else None,
        base_path=payload.base_path,
        host_key_sha256=payload.host_key_sha256,
        host_key_type=payload.host_key_type,
        enabled=payload.enabled,
    )
    db.add(d)
    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        raise conflict("A destination with this name already exists", "destination_name_taken") from exc
    audit.record(db, request, "backup.destination.create", actor=user, entity_type="sftp_destination", entity_id=d.id,
                 details={"name": d.name, "host": d.host})  # fmt: skip
    db.commit()
    return _dest_out(db, d)


@router.put("/destinations/{dest_id}", response_model=DestinationOut)
def update_destination(
    dest_id: uuid.UUID, payload: DestinationUpdate, request: Request, user: AdminUser, db: DbSession
) -> DestinationOut:
    d = _get_dest(db, dest_id)
    _guard_host(payload.host)
    d.name, d.host, d.port, d.username = payload.name, payload.host, payload.port, payload.username
    d.auth_type, d.base_path, d.enabled = payload.auth_type, payload.base_path, payload.enabled
    d.host_key_sha256, d.host_key_type = payload.host_key_sha256, payload.host_key_type or d.host_key_type
    if payload.password:
        d.password_encrypted = encrypt_secret(payload.password)
    if payload.private_key:
        d.private_key_encrypted = encrypt_secret(payload.private_key)
    if d.auth_type == SftpAuthType.PASSWORD and not d.password_encrypted:
        raise ApiError(
            422, "validation_error", "A password is required", [{"field": "password", "message": "Required"}]
        )
    if d.auth_type == SftpAuthType.KEY and not d.private_key_encrypted:
        raise ApiError(
            422, "validation_error", "A private key is required", [{"field": "private_key", "message": "Required"}]
        )
    d.last_test_ok = d.last_test_at = d.last_test_message = None  # the old result described the old settings
    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        raise conflict("A destination with this name already exists", "destination_name_taken") from exc
    audit.record(db, request, "backup.destination.update", actor=user, entity_type="sftp_destination", entity_id=d.id,
                 details={"name": d.name})  # fmt: skip
    db.commit()
    return _dest_out(db, d)


@router.delete("/destinations/{dest_id}", status_code=204)
def delete_destination(dest_id: uuid.UUID, request: Request, user: AdminUser, db: DbSession) -> None:
    d = _get_dest(db, dest_id)
    in_use = db.scalar(
        select(func.count()).select_from(CameraBackupPolicy).where(CameraBackupPolicy.destination_id == d.id)
    )
    if in_use:
        raise conflict(f"{in_use} camera backup policies use this destination. Move them first", "destination_in_use")
    d.deleted_at = utcnow()
    audit.record(db, request, "backup.destination.delete", actor=user, entity_type="sftp_destination", entity_id=d.id,
                 details={"name": d.name})  # fmt: skip
    db.commit()


@router.post("/destinations/{dest_id}/test", response_model=DestinationOut)
def test_destination(dest_id: uuid.UUID, request: Request, user: AdminUser, db: DbSession) -> DestinationOut:
    """Connect (checking the pinned host key), log in and prove we can create a folder, write, read back and delete."""
    d = _get_dest(db, dest_id)
    _guard_host(d.host)
    ok, message = True, "Connected, logged in and wrote a test file under the base path."
    try:
        with SftpSession(backup.target_for(d)) as session:
            session.write_test(d.base_path)
    except SftpError as exc:
        ok, message = False, exc.message
    d.last_test_at, d.last_test_ok, d.last_test_message = utcnow(), ok, message[:512]
    audit.record(db, request, "backup.destination.test", actor=user, entity_type="sftp_destination", entity_id=d.id,
                 success=ok, details={"name": d.name})  # fmt: skip
    db.commit()
    return _dest_out(db, d)


# ------------------------------------------------------------------------------------------------------ policies
def _policy_rows(
    db: Session,
    user: User,
    campus_id: uuid.UUID | None,
    nvr_id: uuid.UUID | None,
    enabled: bool | None,
    camera_ids: list[uuid.UUID] | None,
    page: int,
    page_size: int,
) -> Page[PolicyOut]:
    stmt = (
        select(Camera, Nvr, Campus, CameraBackupPolicy)
        .join(Nvr, Nvr.id == Camera.nvr_id)
        .join(Campus, Campus.id == Camera.campus_id)
        .outerjoin(CameraBackupPolicy, CameraBackupPolicy.camera_id == Camera.id)
        .where(Nvr.deleted_at.is_(None))
    )
    allowed = allowed_campus_ids(db, user)
    if allowed is not None:
        stmt = stmt.where(Camera.campus_id.in_(allowed))
    if campus_id:
        stmt = stmt.where(Camera.campus_id == campus_id)
    if nvr_id:
        stmt = stmt.where(Camera.nvr_id == nvr_id)
    if camera_ids:
        stmt = stmt.where(Camera.id.in_(camera_ids))
    if enabled is True:
        stmt = stmt.where(CameraBackupPolicy.enabled.is_(True))
    elif enabled is False:
        stmt = stmt.where((CameraBackupPolicy.id.is_(None)) | (CameraBackupPolicy.enabled.is_(False)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(Campus.name, Nvr.name, Camera.channel_number).offset((page - 1) * page_size).limit(page_size)
    ).all()
    dest_names = {d.id: d.name for d in db.scalars(select(SftpDestination).where(SftpDestination.deleted_at.is_(None)))}
    last_jobs: dict[uuid.UUID, BackupJob] = {}
    cam_ids = [c.id for c, *_ in rows]
    if cam_ids:
        for j in db.scalars(
            select(BackupJob)
            .where(BackupJob.camera_id.in_(cam_ids), BackupJob.trigger == BackupTrigger.SCHEDULED)
            .order_by(BackupJob.created_at)
        ):
            last_jobs[j.camera_id] = j
    items = []
    for cam, nvr, campus, pol in rows:
        last = last_jobs.get(cam.id)
        items.append(
            PolicyOut(
                camera_id=cam.id,
                camera_name=cam.name,
                channel_number=cam.channel_number,
                nvr_id=nvr.id,
                nvr_name=nvr.name,
                campus_id=campus.id,
                campus_name=campus.name,
                configured=pol is not None,
                enabled=bool(pol and pol.enabled),
                backup_stream=pol.backup_stream if pol else BackupStream.SUB,
                frequency_minutes=pol.frequency_minutes if pol else 60,
                recording_mode=pol.recording_mode if pol else RecordingMode.ALL,
                destination_id=pol.destination_id if pol else None,
                destination_name=dest_names.get(pol.destination_id) if pol and pol.destination_id else None,
                checkpoint_at=pol.checkpoint_at if pol else None,
                last_run_at=pol.last_run_at if pol else None,
                next_run_at=pol.next_run_at if pol else None,
                consecutive_failures=pol.consecutive_failures if pol else 0,
                last_job_status=last.status if last else None,
                last_job_message=last.message if last else None,
            )
        )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/policies", response_model=Page[PolicyOut])
def list_policies(
    user: CurrentUser,
    db: DbSession,
    campus_id: uuid.UUID | None = None,
    nvr_id: uuid.UUID | None = None,
    enabled: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> Page[PolicyOut]:
    return _policy_rows(db, user, campus_id, nvr_id, enabled, None, page, page_size)


def _apply_policy(db: Session, cam: Camera, fields: PolicyFields, *, full: bool, now: datetime) -> CameraBackupPolicy:
    pol = db.scalars(select(CameraBackupPolicy).where(CameraBackupPolicy.camera_id == cam.id)).first()
    created = pol is None
    if pol is None:
        pol = CameraBackupPolicy(camera_id=cam.id)
        db.add(pol)
        pol.enabled, pol.backup_stream, pol.frequency_minutes = False, BackupStream.SUB, 60
        pol.recording_mode = RecordingMode.ALL
    was_enabled = pol.enabled
    for name in ("enabled", "backup_stream", "frequency_minutes", "recording_mode", "destination_id"):
        value = getattr(fields, name)
        if value is not None or (full and name == "destination_id"):
            setattr(pol, name, value)
    if pol.enabled:
        if pol.destination_id is None:
            raise ApiError(
                422, "validation_error", "Choose a destination before enabling backups",
                [{"field": "destination_id", "message": "Required"}],
            )  # fmt: skip
        dest = db.get(SftpDestination, pol.destination_id)
        if dest is None or dest.deleted_at is not None:
            raise ApiError(
                422, "validation_error", "That destination does not exist",
                [{"field": "destination_id", "message": "Unknown destination"}],
            )  # fmt: skip
        if created or not was_enabled or pol.checkpoint_at is None:
            hours = fields.backfill_hours or 0
            pol.checkpoint_at = pol.checkpoint_at or now - timedelta(hours=hours)
            if fields.backfill_hours is not None and (created or not was_enabled):
                pol.checkpoint_at = now - timedelta(hours=hours)
            pol.next_run_at = None  # run as soon as the scheduler looks
            pol.consecutive_failures = 0
    return pol


@router.put("/policies/{camera_id}", response_model=PolicyOut)
def set_policy(
    camera_id: uuid.UUID, payload: PolicyUpdate, request: Request, user: OperatorUser, db: DbSession
) -> PolicyOut:
    cam = _visible_camera(db, user, camera_id)
    _apply_policy(db, cam, payload, full=True, now=utcnow())
    audit.record(
        db, request, "backup.policy.update", actor=user, entity_type="camera", entity_id=cam.id,
        campus_id=cam.campus_id, details={"enabled": payload.enabled, "stream": payload.backup_stream.value},
    )  # fmt: skip
    db.commit()
    return _policy_rows(db, user, None, None, None, [cam.id], 1, 1).items[0]


@router.post("/policies/bulk", response_model=list[PolicyOut])
def bulk_policy(payload: PolicyBulk, request: Request, user: OperatorUser, db: DbSession) -> list[PolicyOut]:
    """Apply only the fields that are given to every selected camera (others keep their own values)."""
    cams = [_visible_camera(db, user, cid) for cid in dict.fromkeys(payload.camera_ids)]
    now = utcnow()
    for cam in cams:
        _apply_policy(db, cam, payload, full=False, now=now)
    changes = payload.model_dump(mode="json", exclude={"camera_ids"}, exclude_none=True)
    audit.record(
        db, request, "backup.policy.bulk", actor=user, entity_type="camera", details={"cameras": len(cams), **changes}
    )
    db.commit()
    return _policy_rows(db, user, None, None, None, [c.id for c in cams], 1, 200).items


# ---------------------------------------------------------------------------------------------------- recordings
@router.get("/cameras/{camera_id}/recordings", response_model=RecordingSearchOut)
def search_recordings(
    camera_id: uuid.UUID,
    user: OperatorUser,
    db: DbSession,
    start: datetime,
    end: datetime,
    destination_id: uuid.UUID | None = None,
) -> RecordingSearchOut:
    """Ask the NVR which recordings exist in the window and show which of them are already backed up."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ApiError(422, "validation_error", "start and end must include a timezone (use UTC, e.g. ...Z)")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start or end - start > timedelta(hours=MAX_SEARCH_HOURS):
        raise ApiError(422, "validation_error", f"Search a window of up to {MAX_SEARCH_HOURS} hours (end after start)")
    cam = _visible_camera(db, user, camera_id)
    nvr = db.get(Nvr, cam.nvr_id)
    assert nvr is not None
    _require_usable_nvr(nvr)
    assert nvr.utc_offset_minutes is not None
    offset = nvr.utc_offset_minutes
    try:
        with get_adapter(nvr) as adapter:
            found = adapter.search_recordings(
                cam.channel_number, backup.utc_to_local(start, offset), backup.utc_to_local(end, offset)
            )
    except NvrError as exc:
        raise ApiError(502, "nvr_error", exc.message) from exc

    pol = db.scalars(select(CameraBackupPolicy).where(CameraBackupPolicy.camera_id == cam.id)).first()
    dest_id = destination_id or (pol.destination_id if pol else None)
    done: dict[tuple[BackupStream, datetime], BackupFile] = {}
    if dest_id:
        for f in db.scalars(
            select(BackupFile).where(BackupFile.camera_id == cam.id, BackupFile.destination_id == dest_id)
        ):
            done[(f.stream, f.recording_start)] = f
    margin = timedelta(seconds=get_settings().backup_inprogress_margin_seconds)
    now_local = backup.utc_to_local(utcnow(), offset)
    items: list[RecordingOut] = []
    for rec in found:
        stream = BackupStream.MAIN if rec.stream == StreamKind.MAIN else BackupStream.SUB
        row = done.get((stream, backup.local_to_utc(rec.start, offset)))
        items.append(
            RecordingOut(
                start=backup.local_to_utc(rec.start, offset),
                end=backup.local_to_utc(rec.end, offset),
                stream=rec.stream,
                size_bytes=rec.size_bytes,
                kind=rec.kind,
                in_progress=rec.end > now_local - margin,
                backup_status=row.status if row else None,
                backup_file_id=row.id if row else None,
                remote_path=row.remote_path if row else None,
            )
        )
    return RecordingSearchOut(
        camera_id=cam.id, searched_from=start, searched_to=end, utc_offset_minutes=offset, items=items
    )


def _require_usable_nvr(nvr: Nvr) -> None:
    if not nvr.enabled or nvr.connection_type == ConnectionType.P2P:
        raise ApiError(409, "nvr_not_usable", "This NVR is disabled or uses P2P, which cannot be used for recordings")
    if nvr.status == NvrStatus.AUTH_FAILED:
        raise ApiError(409, "nvr_not_usable", "The NVR rejected its credentials. Update them and test the NVR first")
    if nvr.last_success_at is None or nvr.utc_offset_minutes is None:
        raise ApiError(
            409,
            "nvr_not_usable",
            "This NVR has not connected successfully yet (its timezone is unknown). Test it first",
        )


# --------------------------------------------------------------------------------------------------------- jobs
def _job_out(db: Session, jobs: list[BackupJob]) -> list[JobOut]:
    if not jobs:
        return []
    cams = {c.id: c for c in db.scalars(select(Camera).where(Camera.id.in_({j.camera_id for j in jobs})))}
    nvrs = {n.id: n for n in db.scalars(select(Nvr).where(Nvr.id.in_({j.nvr_id for j in jobs})))}
    campuses = {c.id: c for c in db.scalars(select(Campus).where(Campus.id.in_({j.campus_id for j in jobs})))}
    dests = {
        d.id: d
        for d in db.scalars(select(SftpDestination).where(SftpDestination.id.in_({j.destination_id for j in jobs})))
    }
    users = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_({j.requested_by_id for j in jobs if j.requested_by_id})))
    }
    return [
        JobOut(
            id=j.id,
            camera_id=j.camera_id,
            camera_name=cams[j.camera_id].name,
            channel_number=cams[j.camera_id].channel_number,
            nvr_id=j.nvr_id,
            nvr_name=nvrs[j.nvr_id].name,
            campus_id=j.campus_id,
            campus_name=campuses[j.campus_id].name,
            destination_id=j.destination_id,
            destination_name=dests[j.destination_id].name,
            stream=j.stream,
            trigger=j.trigger,
            status=j.status,
            window_start=j.window_start,
            window_end=j.window_end,
            cancel_requested=j.cancel_requested,
            files_total=j.files_total,
            files_done=j.files_done,
            files_failed=j.files_failed,
            files_skipped=j.files_skipped,
            bytes_total=j.bytes_total,
            bytes_done=j.bytes_done,
            message=j.message,
            requested_by=users[j.requested_by_id].username if j.requested_by_id in users else None,
            created_at=j.created_at,
            started_at=j.started_at,
            finished_at=j.finished_at,
        )
        for j in jobs
    ]


def _visible_job(db: Session, user: User, job_id: uuid.UUID) -> BackupJob:
    job = db.get(BackupJob, job_id)
    allowed = allowed_campus_ids(db, user)
    if job is None or (allowed is not None and job.campus_id not in allowed):
        raise not_found("Backup job")
    return job


def _dispatch(db: Session, job: BackupJob) -> None:
    try:
        enqueue_backup(job.id)
    except QueueUnavailableError:
        # The job stays queued: the sweeper re-dispatches it as soon as the queue is back.
        job.message = "Waiting for the background worker (the queue is unavailable)"
        db.commit()


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_manual_job(payload: JobCreate, request: Request, user: OperatorUser, db: DbSession) -> JobOut:
    cam = _visible_camera(db, user, payload.camera_id)
    nvr = db.get(Nvr, cam.nvr_id)
    assert nvr is not None
    _require_usable_nvr(nvr)
    pol = db.scalars(select(CameraBackupPolicy).where(CameraBackupPolicy.camera_id == cam.id)).first()
    dest_id = payload.destination_id or (pol.destination_id if pol else None)
    if dest_id is None:
        raise ApiError(
            422, "validation_error", "Choose a destination", [{"field": "destination_id", "message": "Required"}]
        )
    dest = _get_dest(db, dest_id)
    if not dest.enabled:
        raise ApiError(409, "destination_disabled", "This destination is disabled")
    if payload.end - payload.start > timedelta(days=MAX_MANUAL_DAYS):
        raise ApiError(422, "validation_error", f"A manual backup covers at most {MAX_MANUAL_DAYS} days")
    if payload.end > utcnow() + timedelta(minutes=5):
        raise ApiError(422, "validation_error", "The window cannot end in the future")
    if backup.active_job_for_camera(db, cam.id) is not None:
        raise conflict("A backup for this camera is already queued or running", "backup_already_active")
    stream = payload.stream or (pol.backup_stream if pol else BackupStream.SUB)
    job = backup.create_job(db, cam, nvr, dest, stream, BackupTrigger.MANUAL, payload.start, payload.end, user.id)
    audit.record(db, request, "backup.job.create", actor=user, entity_type="backup_job", entity_id=job.id,
                 campus_id=cam.campus_id, details={"camera": cam.name, "stream": stream.value})  # fmt: skip
    db.commit()
    _dispatch(db, job)
    return _job_out(db, [job])[0]


@router.get("/jobs", response_model=Page[JobOut])
def list_jobs(
    user: CurrentUser,
    db: DbSession,
    status: BackupJobStatus | None = None,
    active: bool = False,
    campus_id: uuid.UUID | None = None,
    nvr_id: uuid.UUID | None = None,
    camera_id: uuid.UUID | None = None,
    trigger: BackupTrigger | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[JobOut]:
    stmt = select(BackupJob)
    allowed = allowed_campus_ids(db, user)
    if allowed is not None:
        stmt = stmt.where(BackupJob.campus_id.in_(allowed))
    if status:
        stmt = stmt.where(BackupJob.status == status)
    if active:
        stmt = stmt.where(BackupJob.status.in_(backup.ACTIVE_JOB))
    if campus_id:
        stmt = stmt.where(BackupJob.campus_id == campus_id)
    if nvr_id:
        stmt = stmt.where(BackupJob.nvr_id == nvr_id)
    if camera_id:
        stmt = stmt.where(BackupJob.camera_id == camera_id)
    if trigger:
        stmt = stmt.where(BackupJob.trigger == trigger)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(BackupJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)))
    return Page(items=_job_out(db, rows), total=total, page=page, page_size=page_size)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: uuid.UUID, user: CurrentUser, db: DbSession) -> JobDetail:
    job = _visible_job(db, user, job_id)
    files = db.scalars(select(BackupFile).where(BackupFile.job_id == job.id).order_by(BackupFile.recording_start))
    out = _job_out(db, [job])[0]
    return JobDetail(
        **out.model_dump(),
        files=[
            FileOut(
                id=f.id,
                status=f.status,
                recording_start=f.recording_start,
                recording_end=f.recording_end,
                size_bytes=f.size_bytes,
                bytes_done=f.bytes_done,
                attempts=f.attempts,
                error=f.error,
                remote_path=f.remote_path,
                sha256=f.sha256,
            )  # fmt: skip
            for f in files
        ],
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, request: Request, user: OperatorUser, db: DbSession) -> JobOut:
    job = _visible_job(db, user, job_id)
    if job.status not in backup.ACTIVE_JOB:
        raise conflict("This job has already finished", "job_finished")
    backup.request_cancel(db, job)
    audit.record(db, request, "backup.job.cancel", actor=user, entity_type="backup_job", entity_id=job.id,
                 campus_id=job.campus_id)  # fmt: skip
    db.commit()
    return _job_out(db, [job])[0]


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=201)
def retry_job(job_id: uuid.UUID, request: Request, user: OperatorUser, db: DbSession) -> JobOut:
    """Run the same window again (failed recordings get a fresh set of attempts)."""
    old = _visible_job(db, user, job_id)
    if old.status in backup.ACTIVE_JOB:
        raise conflict("This job is still queued or running", "job_active")
    cam, nvr, dest = db.get(Camera, old.camera_id), db.get(Nvr, old.nvr_id), db.get(SftpDestination, old.destination_id)
    if cam is None or nvr is None or dest is None or dest.deleted_at is not None:
        raise not_found("Camera, NVR or destination")
    _require_usable_nvr(nvr)
    if backup.active_job_for_camera(db, cam.id) is not None:
        raise conflict("A backup for this camera is already queued or running", "backup_already_active")
    for f in db.scalars(
        select(BackupFile).where(BackupFile.job_id == old.id, BackupFile.status == BackupFileStatus.FAILED)
    ):
        f.attempts = 0
    job = backup.create_job(
        db, cam, nvr, dest, old.stream, BackupTrigger.MANUAL, old.window_start, old.window_end, user.id
    )
    audit.record(db, request, "backup.job.retry", actor=user, entity_type="backup_job", entity_id=job.id,
                 campus_id=cam.campus_id, details={"of": str(old.id)})  # fmt: skip
    db.commit()
    _dispatch(db, job)
    return _job_out(db, [job])[0]


# --------------------------------------------------------------------------------------------------------- status
def _worker_online() -> tuple[bool | None, str]:
    from app.workers.celery_app import celery_app

    if get_settings().celery_task_always_eager:
        return True, "Tasks run inline (eager mode)"
    try:
        queues = celery_app.control.inspect(timeout=1.0).active_queues()
    except Exception:
        return None, "The task queue (Redis) is not reachable"
    if not queues:
        return (
            False,
            "No background worker is running. Start one with: "
            "celery -A app.workers.celery_app worker -Q celery,backups --pool=solo",
        )
    if not any(q.get("name") == "backups" for qs in queues.values() for q in qs):
        return False, "A worker is running but none listens to the 'backups' queue (start it with -Q celery,backups)"
    return True, "Worker online"


@router.get("/status", response_model=BackupStatusOut)
def backup_status(user: CurrentUser, db: DbSession) -> BackupStatusOut:
    allowed = allowed_campus_ids(db, user)
    since = utcnow() - timedelta(hours=24)

    def count(*conds: ColumnElement[bool]) -> int:
        stmt = select(func.count()).select_from(BackupJob).where(*conds)
        if allowed is not None:
            stmt = stmt.where(BackupJob.campus_id.in_(allowed))
        return db.scalar(stmt) or 0

    bytes_stmt = select(func.coalesce(func.sum(BackupJob.bytes_done), 0)).where(BackupJob.created_at >= since)
    if allowed is not None:
        bytes_stmt = bytes_stmt.where(BackupJob.campus_id.in_(allowed))
    online, detail = _worker_online()
    root = Path(get_settings().backup_staging_dir)
    free: float | None = None
    try:
        free = round(shutil.disk_usage(root if root.exists() else root.parent).free / 1024**3, 1)
    except OSError:
        free = None
    failing = db.scalar(
        select(func.count())
        .select_from(SftpDestination)
        .where(SftpDestination.deleted_at.is_(None), SftpDestination.last_test_ok.is_(False))
    )
    return BackupStatusOut(
        worker_online=online,
        detail=detail,
        active_jobs=count(BackupJob.status == BackupJobStatus.RUNNING),
        queued_jobs=count(BackupJob.status == BackupJobStatus.QUEUED),
        failed_last_24h=count(BackupJob.status == BackupJobStatus.FAILED, BackupJob.created_at >= since),
        bytes_last_24h=db.scalar(bytes_stmt) or 0,
        destinations_failing=failing or 0,
        staging_free_gb=free,
    )
