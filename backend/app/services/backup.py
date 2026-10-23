"""Backup engine: copies NVR recordings to an SFTP destination.

Flow of one job (one camera, one time window), all of it resumable by running the job again:
  search the NVR -> drop recordings still being written -> for each recording not already backed up:
  download to the staging folder (streamed) -> check size -> hash -> upload under a temporary name -> verify the
  size on the server -> rename into place -> write a .sha256 sidecar -> delete the staged copy.

Nothing is ever marked done before the server confirmed it. A recording is identified by (camera, destination,
stream, start time), so running a job twice never uploads anything twice.
"""

import hashlib
import logging
import re
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.db.base import utcnow
from app.integrations.adapter_factory import get_adapter
from app.integrations.base import (
    CancelledDownload,
    NvrAdapter,
    NvrAuthError,
    NvrError,
    NvrUnreachableError,
    RecordingFile,
)
from app.integrations.hostguard import HostNotAllowedError
from app.integrations.sftp import (
    SftpAuthError,
    SftpCancelled,
    SftpError,
    SftpHostKeyError,
    SftpSession,
    SftpTarget,
    SftpUnreachableError,
    clean_remote_path,
)
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
from app.services import nvr_ops

log = logging.getLogger(__name__)

ACTIVE_JOB = (BackupJobStatus.QUEUED, BackupJobStatus.RUNNING)
PROGRESS_EVERY = 1.5  # seconds between progress writes


class JobAbort(Exception):
    """Stop the whole job (not just one file) with this status and message."""

    def __init__(self, status: BackupJobStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ------------------------------------------------------------------------------------------------- time and paths
def local_to_utc(local: datetime, offset_minutes: int) -> datetime:
    """NVR wall-clock time (naive) to UTC, using the offset measured from the NVR's own clock."""
    return (local - timedelta(minutes=offset_minutes)).replace(tzinfo=UTC)


def utc_to_local(utc: datetime, offset_minutes: int) -> datetime:
    return (utc.astimezone(UTC) + timedelta(minutes=offset_minutes)).replace(tzinfo=None)


def slug(value: str, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return s[:60] or fallback


def remote_path_for(dest: SftpDestination, campus: Campus, nvr: Nvr, camera: Camera, f: BackupFile) -> str:
    start = f.recording_start.astimezone(UTC)
    end = f.recording_end.astimezone(UTC)
    name = f"{start:%Y%m%dT%H%M%SZ}_{end:%Y%m%dT%H%M%SZ}.dav"
    return clean_remote_path(
        dest.base_path,
        slug(campus.name, "campus"),
        slug(nvr.name, "nvr"),
        f"ch{camera.channel_number:02d}-{slug(camera.name, 'camera')}",
        f.stream.value,
        f"{start:%Y}",
        f"{start:%m}",
        f"{start:%d}",
        name,
    )


def target_for(dest: SftpDestination) -> SftpTarget:
    return SftpTarget(
        host=dest.host,
        port=dest.port,
        username=dest.username,
        password=decrypt_secret(dest.password_encrypted)
        if dest.auth_type == SftpAuthType.PASSWORD and dest.password_encrypted
        else None,
        private_key=decrypt_secret(dest.private_key_encrypted)
        if dest.auth_type == SftpAuthType.KEY and dest.private_key_encrypted
        else None,
        base_path=dest.base_path,
        host_key_sha256=dest.host_key_sha256,
    )


# ------------------------------------------------------------------------------------------------------ job setup
def active_job_for_camera(db: Session, camera_id: uuid.UUID) -> BackupJob | None:
    return db.scalars(
        select(BackupJob).where(BackupJob.camera_id == camera_id, BackupJob.status.in_(ACTIVE_JOB)).limit(1)
    ).first()


def create_job(
    db: Session,
    camera: Camera,
    nvr: Nvr,
    dest: SftpDestination,
    stream: BackupStream,
    trigger: BackupTrigger,
    start: datetime,
    end: datetime,
    user_id: uuid.UUID | None = None,
) -> BackupJob:
    job = BackupJob(
        camera_id=camera.id,
        nvr_id=nvr.id,
        campus_id=camera.campus_id,
        destination_id=dest.id,
        stream=stream,
        trigger=trigger,
        requested_by_id=user_id,
        window_start=start.astimezone(UTC),
        window_end=end.astimezone(UTC),
        status=BackupJobStatus.QUEUED,
    )
    db.add(job)
    db.flush()
    return job


def schedule_due(db: Session, now: datetime | None = None) -> list[uuid.UUID]:
    """Create a job for every enabled policy that is due. One active job per camera at most. Returns the new ids."""
    now = now or utcnow()
    s = get_settings()
    rows = db.execute(
        select(CameraBackupPolicy, Camera, Nvr, SftpDestination)
        .join(Camera, Camera.id == CameraBackupPolicy.camera_id)
        .join(Nvr, Nvr.id == Camera.nvr_id)
        .join(SftpDestination, SftpDestination.id == CameraBackupPolicy.destination_id)
        .where(
            CameraBackupPolicy.enabled.is_(True),
            Nvr.deleted_at.is_(None),
            Nvr.enabled.is_(True),
            Nvr.connection_type != ConnectionType.P2P,
            Nvr.status.not_in([NvrStatus.AUTH_FAILED, NvrStatus.PENDING, NvrStatus.UNSUPPORTED]),
            SftpDestination.deleted_at.is_(None),
            SftpDestination.enabled.is_(True),
            (CameraBackupPolicy.next_run_at.is_(None)) | (CameraBackupPolicy.next_run_at <= now),
        )
    ).all()
    created: list[uuid.UUID] = []
    for policy, camera, nvr, dest in rows:
        if active_job_for_camera(db, camera.id) is not None:
            continue
        start = (policy.checkpoint_at or now - timedelta(hours=1)) - timedelta(minutes=s.backup_overlap_minutes)
        job = create_job(db, camera, nvr, dest, policy.backup_stream, BackupTrigger.SCHEDULED, start, now)
        created.append(job.id)
    db.commit()
    return created


def request_cancel(db: Session, job: BackupJob) -> BackupJob:
    if job.status == BackupJobStatus.QUEUED:
        job.status, job.finished_at, job.message = BackupJobStatus.CANCELLED, utcnow(), "Cancelled before it started"
    elif job.status == BackupJobStatus.RUNNING:
        job.cancel_requested = True
    db.commit()
    return job


def recover_stale(db: Session, now: datetime | None = None) -> list[uuid.UUID]:
    """A worker that died leaves its job `running` with a stale heartbeat. Put such jobs back in the queue (their
    unfinished files return to pending and the staged copies are deleted) and return the ids to dispatch again. Also
    returns queued jobs whose message was never picked up."""
    now = now or utcnow()
    s = get_settings()
    stale = now - timedelta(seconds=s.backup_job_stale_seconds)
    redo: list[uuid.UUID] = []
    jobs = db.scalars(
        select(BackupJob).where(
            BackupJob.status == BackupJobStatus.RUNNING,
            (BackupJob.heartbeat_at.is_(None)) | (BackupJob.heartbeat_at < stale),
        )
    ).all()
    for job in jobs:
        db.execute(
            update(BackupFile)
            .where(
                BackupFile.job_id == job.id,
                BackupFile.status.in_(
                    [BackupFileStatus.DOWNLOADING, BackupFileStatus.UPLOADING, BackupFileStatus.VERIFYING]
                ),
            )
            .values(status=BackupFileStatus.PENDING, bytes_done=0)
        )
        shutil.rmtree(_staging_root() / str(job.id), ignore_errors=True)
        job.status, job.message = BackupJobStatus.QUEUED, "Recovered after the worker stopped; resuming"
        job.heartbeat_at = None
        redo.append(job.id)
    lost = db.scalars(
        select(BackupJob.id).where(
            BackupJob.status == BackupJobStatus.QUEUED, BackupJob.created_at < now - timedelta(minutes=2)
        )
    ).all()
    db.commit()
    return redo + [j for j in lost if j not in redo]


def _staging_root() -> Path:
    return Path(get_settings().backup_staging_dir)


# ---------------------------------------------------------------------------------------------------- the engine
class _Progress:
    """Writes progress to the database at most every PROGRESS_EVERY seconds and notices cancel requests."""

    def __init__(self, db: Session, job: BackupJob, file: BackupFile | None = None, count_job: bool = True) -> None:
        self.db, self.job, self.file, self.count_job = db, job, file, count_job
        self.base_job_bytes = job.bytes_done
        self._last = 0.0
        self.cancel = job.cancel_requested

    def update(self, done: int) -> None:
        now = time.monotonic()
        if now - self._last < PROGRESS_EVERY:
            return
        self._last = now
        if self.file is not None:
            self.file.bytes_done = done
        if self.count_job:
            self.job.bytes_done = self.base_job_bytes + done
        self.job.heartbeat_at = utcnow()
        self.db.commit()
        self.db.refresh(self.job, ["cancel_requested"])
        self.cancel = self.job.cancel_requested

    def cancelled(self) -> bool:
        return self.cancel


def claim(db: Session, job_id: uuid.UUID) -> BackupJob | None:
    """Atomically move a queued job to running. Only one backup job runs per NVR at a time: the NVR has little
    capacity to spare and a second parallel download would slow down both (parallel downloads are untested)."""
    job = db.get(BackupJob, job_id)
    if job is None:
        return None
    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": job.nvr_id.int & 0x7FFFFFFFFFFFFFFF})
    db.refresh(job)
    if job.status != BackupJobStatus.QUEUED:
        db.rollback()
        return None
    fresh = utcnow() - timedelta(seconds=get_settings().backup_job_stale_seconds)
    busy = db.scalar(
        select(BackupJob.id)
        .where(
            BackupJob.nvr_id == job.nvr_id,
            BackupJob.status == BackupJobStatus.RUNNING,
            BackupJob.heartbeat_at > fresh,
            BackupJob.id != job.id,
        )
        .limit(1)
    )
    if busy is not None:
        db.rollback()
        return None  # stays queued; the sweeper hands it out again
    now = utcnow()
    job.status, job.started_at, job.heartbeat_at, job.message = BackupJobStatus.RUNNING, now, now, None
    db.commit()
    return job


def run_job(db: Session, job_id: uuid.UUID) -> BackupJobStatus | None:
    job = claim(db, job_id)
    if job is None:
        return None
    try:
        status, message = _run(db, job)
    except JobAbort as abort:
        status, message = abort.status, abort.message
    except Exception as exc:  # noqa: BLE001 - a worker must always leave the job in a resolved state
        import traceback

        log.error(
            "Unexpected %s in backup job %s\n%s",
            type(exc).__name__,
            job.id,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        status, message = BackupJobStatus.FAILED, "Unexpected error. See the server log."
    finally:
        shutil.rmtree(_staging_root() / str(job.id), ignore_errors=True)
    _finish(db, job, status, message)
    return status


def _finish(db: Session, job: BackupJob, status: BackupJobStatus, message: str | None) -> None:
    db.rollback()
    job = db.get(BackupJob, job.id) or job
    now = utcnow()
    job.status, job.finished_at, job.heartbeat_at = status, now, now
    if message:
        job.message = message[:1024]
    policy = db.scalars(select(CameraBackupPolicy).where(CameraBackupPolicy.camera_id == job.camera_id)).first()
    if policy is not None and job.trigger == BackupTrigger.SCHEDULED:
        policy.last_run_at = now
        if status in (BackupJobStatus.SUCCESS, BackupJobStatus.PARTIAL):
            # Checkpoint advances only past recordings that are all safely backed up (see _advance_checkpoint).
            policy.consecutive_failures = 0 if status == BackupJobStatus.SUCCESS else policy.consecutive_failures + 1
        elif status == BackupJobStatus.FAILED:
            policy.consecutive_failures += 1
        wait = timedelta(minutes=policy.frequency_minutes)
        if policy.consecutive_failures:
            wait = min(wait, timedelta(minutes=min(60, 2 ** min(policy.consecutive_failures, 6))))
        policy.next_run_at = now + wait
    db.commit()


def _fail_destination(db: Session, dest: SftpDestination, message: str) -> None:
    dest.last_test_at, dest.last_test_ok, dest.last_test_message = utcnow(), False, message[:512]
    db.commit()


def _run(db: Session, job: BackupJob) -> tuple[BackupJobStatus, str | None]:
    s = get_settings()
    camera = db.get(Camera, job.camera_id)
    nvr = db.get(Nvr, job.nvr_id)
    dest = db.get(SftpDestination, job.destination_id)
    campus = db.get(Campus, job.campus_id)
    if camera is None or nvr is None or dest is None or campus is None or nvr.deleted_at or dest.deleted_at:
        raise JobAbort(BackupJobStatus.FAILED, "The camera, NVR or destination no longer exists")
    if not nvr.enabled or nvr.connection_type == ConnectionType.P2P:
        raise JobAbort(BackupJobStatus.FAILED, "This NVR is disabled or cannot be used for backups")
    if nvr.status == NvrStatus.AUTH_FAILED:
        raise JobAbort(BackupJobStatus.FAILED, "The NVR rejected its credentials. Update them, then test the NVR")
    if not dest.enabled:
        raise JobAbort(BackupJobStatus.FAILED, "The destination is disabled")
    if nvr.utc_offset_minutes is None:
        raise JobAbort(
            BackupJobStatus.FAILED,
            "The NVR's timezone offset is unknown, so recording times cannot be converted. Run Test connection first",
        )
    offset = nvr.utc_offset_minutes
    policy = db.scalars(select(CameraBackupPolicy).where(CameraBackupPolicy.camera_id == camera.id)).first()
    mode = policy.recording_mode if policy and job.trigger == BackupTrigger.SCHEDULED else RecordingMode.ALL
    want = StreamKind.MAIN if job.stream == BackupStream.MAIN else StreamKind.SUB

    # 1. Find recordings.
    try:
        adapter = get_adapter(nvr)
        with adapter:
            found = adapter.search_recordings(
                camera.channel_number,
                utc_to_local(job.window_start, offset),
                utc_to_local(job.window_end, offset),
            )
            now_local = utc_to_local(utcnow(), offset)
            plan = _plan(db, job, camera, found, want, mode, offset, now_local, s.backup_inprogress_margin_seconds)
            _transfer(db, job, adapter, plan, camera, nvr, dest, campus)
    except NvrAuthError as exc:
        nvr_ops.restore(db, nvr.id, NvrStatus.AUTH_FAILED, exc.message)
        raise JobAbort(
            BackupJobStatus.FAILED, exc.message + " Not retrying: repeated bad logins lock the NVR account"
        ) from exc
    except NvrUnreachableError as exc:
        raise JobAbort(
            BackupJobStatus.FAILED, f"The NVR is unreachable: {exc.message} Will retry on the next run"
        ) from exc
    except HostNotAllowedError as exc:
        raise JobAbort(BackupJobStatus.FAILED, exc.message) from exc
    except NvrError as exc:
        raise JobAbort(BackupJobStatus.FAILED, exc.message) from exc

    # 2. Result.
    db.refresh(job)
    files = db.scalars(select(BackupFile).where(BackupFile.job_id == job.id)).all()
    failed = [f for f in files if f.status == BackupFileStatus.FAILED]
    cancelled = [f for f in files if f.status == BackupFileStatus.CANCELLED]
    done = [f for f in files if f.status == BackupFileStatus.DONE]
    job.files_total, job.files_done, job.files_failed = len(files), len(done), len(failed)
    _advance_checkpoint(db, job, policy, plan, offset)
    db.commit()
    note = " ".join(plan.notes)
    if job.cancel_requested or cancelled:
        return BackupJobStatus.CANCELLED, ("Cancelled. " + note).strip()
    if failed:
        first = failed[0].error or "unknown error"
        status = BackupJobStatus.PARTIAL if done else BackupJobStatus.FAILED
        return status, f"{len(failed)} of {len(files)} recordings failed. First error: {first}. {note}".strip()
    if not files and not plan.notes:
        return BackupJobStatus.SUCCESS, "Nothing new to back up"
    return BackupJobStatus.SUCCESS, note or None


class _Plan:
    def __init__(self) -> None:
        self.todo: list[tuple[BackupFile, RecordingFile]] = []
        self.ordered: list[tuple[datetime, datetime, bool]] = []  # (start, end, safely_backed_up_or_skippable)
        self.notes: list[str] = []
        self.in_progress_from: datetime | None = None


def _plan(
    db: Session,
    job: BackupJob,
    camera: Camera,
    found: list[RecordingFile],
    want: StreamKind,
    mode: RecordingMode,
    offset: int,
    now_local: datetime,
    margin_seconds: int,
) -> _Plan:
    plan = _Plan()
    s = get_settings()
    margin = timedelta(seconds=margin_seconds)
    candidates = [r for r in found if r.stream == want]
    if mode == RecordingMode.CONTINUOUS:
        candidates = [r for r in candidates if r.kind == "Timing"]
    elif mode in (RecordingMode.MOTION, RecordingMode.EVENT):
        candidates = [r for r in candidates if r.kind != "Timing"]
    candidates.sort(key=lambda r: r.start)

    skipped_inprogress = 0
    previous_end: datetime | None = None
    for rec in candidates:
        start_utc, end_utc = local_to_utc(rec.start, offset), local_to_utc(rec.end, offset)
        if previous_end is not None and start_utc - previous_end > timedelta(minutes=2):
            plan.notes.append(
                f"No recording on the NVR from {previous_end:%Y-%m-%d %H:%M} to {start_utc:%H:%M} UTC (gap)."
            )
        previous_end = max(previous_end, end_utc) if previous_end else end_utc
        if rec.end > now_local - margin:  # still being written: the NVR file is incomplete
            skipped_inprogress += 1
            plan.in_progress_from = (
                start_utc if plan.in_progress_from is None else min(plan.in_progress_from, start_utc)
            )
            continue
        row = db.scalars(
            select(BackupFile).where(
                BackupFile.camera_id == camera.id,
                BackupFile.destination_id == job.destination_id,
                BackupFile.stream == job.stream,
                BackupFile.recording_start == start_utc,
            )
        ).first()
        if row is not None and row.status == BackupFileStatus.DONE:
            plan.ordered.append((start_utc, end_utc, True))
            continue
        if row is not None and row.attempts >= s.backup_max_attempts and job.trigger == BackupTrigger.SCHEDULED:
            plan.ordered.append(
                (start_utc, end_utc, False)
            )  # gave up: needs a manual retry, and it blocks the checkpoint
            continue
        if row is None:
            row = BackupFile(
                job_id=job.id,
                camera_id=camera.id,
                destination_id=job.destination_id,
                stream=job.stream,
                nvr_path=rec.path,
                recording_start=start_utc,
                recording_end=end_utc,
                size_bytes=rec.size_bytes,
            )
            db.add(row)
        else:  # retry of an earlier failed/interrupted attempt: this job adopts it
            row.job_id, row.nvr_path, row.recording_end, row.size_bytes = job.id, rec.path, end_utc, rec.size_bytes
            row.status, row.error, row.bytes_done = BackupFileStatus.PENDING, None, 0
        plan.todo.append((row, rec))
        plan.ordered.append((start_utc, end_utc, False))
    if skipped_inprogress:
        job.files_skipped = skipped_inprogress
        plan.notes.append(f"{skipped_inprogress} recording(s) still being written were left for the next run.")
    job.files_total = len(plan.todo)
    job.bytes_total = sum(r.size_bytes or 0 for _, r in plan.todo)
    db.commit()
    return plan


def _transfer(
    db: Session,
    job: BackupJob,
    adapter: NvrAdapter,
    plan: _Plan,
    camera: Camera,
    nvr: Nvr,
    dest: SftpDestination,
    campus: Campus,
) -> None:
    s = get_settings()
    root = _staging_root() / str(job.id)
    root.mkdir(parents=True, exist_ok=True)
    sftp: SftpSession | None = None
    try:
        for row, rec in plan.todo:
            db.refresh(job, ["cancel_requested"])
            if job.cancel_requested:
                row.status = BackupFileStatus.CANCELLED
                db.commit()
                continue
            need = (rec.size_bytes or 0) * 1.1 + s.backup_min_free_gb * 1024**3
            free = shutil.disk_usage(root).free
            if free < need:
                row.status, row.error = BackupFileStatus.FAILED, "Not enough free disk space in the staging folder"
                db.commit()
                raise JobAbort(
                    BackupJobStatus.FAILED,
                    f"Not enough free disk space for backups ({free / 1024**3:.1f} GB free). Free some space or move "
                    "BACKUP_STAGING_DIR to a bigger disk",
                )
            row.attempts += 1
            row.started_at, row.error = utcnow(), None
            row.status, row.bytes_done = BackupFileStatus.DOWNLOADING, 0
            db.commit()
            staged = root / f"{row.id}.dav"
            progress = _Progress(db, job, row)
            base_bytes = job.bytes_done
            try:
                got = adapter.download_recording(rec, staged, progress.update, progress.cancelled)
                _check_size(got, rec)
                row.sha256 = _sha256(staged)
                row.status, row.bytes_done = BackupFileStatus.UPLOADING, got
                db.commit()
                if sftp is None:
                    sftp = SftpSession(target_for(dest))
                    sftp.connect()
                remote = remote_path_for(dest, campus, nvr, camera, row)
                up = _Progress(db, job, row, count_job=False)
                sftp.upload(staged, remote, up.update, up.cancelled)
                row.status = BackupFileStatus.VERIFYING
                db.commit()
                _write_sidecar(sftp, remote, row.sha256, staged.stat().st_size)
                row.remote_path, row.status, row.finished_at = remote, BackupFileStatus.DONE, utcnow()
                job.files_done += 1
                job.bytes_done = base_bytes + got
                job.heartbeat_at = utcnow()
                db.commit()
            except (CancelledDownload, SftpCancelled):
                row.status = BackupFileStatus.CANCELLED
                db.commit()
            except (SftpAuthError, SftpHostKeyError) as exc:
                row.status, row.error = BackupFileStatus.PENDING, exc.message
                db.commit()
                _fail_destination(db, dest, exc.message)
                raise JobAbort(BackupJobStatus.FAILED, f"SFTP destination problem: {exc.message}") from exc
            except SftpUnreachableError as exc:
                row.status, row.error = BackupFileStatus.PENDING, exc.message
                db.commit()
                raise JobAbort(
                    BackupJobStatus.FAILED, f"The SFTP server is unreachable: {exc.message} Will retry on the next run"
                ) from exc
            except (NvrAuthError, NvrUnreachableError):
                row.status = BackupFileStatus.PENDING
                db.commit()
                raise  # the caller decides: auth means stop and do not retry, unreachable means try later
            except (SftpError, NvrError, OSError) as exc:
                msg = exc.message if isinstance(exc, SftpError | NvrError) else f"Local file error: {exc}"
                row.status, row.error, row.finished_at = BackupFileStatus.FAILED, msg[:1024], utcnow()
                if isinstance(exc, SftpError):
                    _drop(sftp)
                    sftp = None  # reconnect for the next file
                db.commit()
            finally:
                staged.unlink(missing_ok=True)
    finally:
        _drop(sftp)


def _drop(sftp: SftpSession | None) -> None:
    if sftp is not None:
        sftp.close()


def _check_size(got: int, rec: RecordingFile) -> None:
    if got <= 0:
        raise NvrError("The NVR sent an empty file")
    expected = rec.size_bytes
    if expected and got < expected * 0.99:
        raise NvrError(f"Incomplete download: received {got} bytes but the NVR lists {expected}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _write_sidecar(sftp: SftpSession, remote: str, digest: str | None, size: int) -> None:
    """A tiny text file next to the recording, so integrity can be checked later without this application."""
    side = remote + ".sha256"
    with sftp.sftp.open(side, "wb") as fh:
        fh.write(f"{digest}  {remote.rsplit('/', 1)[-1]}\n# size={size}\n".encode())


def _advance_checkpoint(
    db: Session, job: BackupJob, policy: CameraBackupPolicy | None, plan: _Plan, offset: int
) -> None:
    """Move the policy's checkpoint forward over recordings that are all done, stopping at the first one that is
    not (failed, given up, or still being written). Manual jobs never move it."""
    if policy is None or job.trigger != BackupTrigger.SCHEDULED:
        return
    rows = {
        r.recording_start: r.status
        for r in db.scalars(select(BackupFile).where(BackupFile.camera_id == job.camera_id)).all()
        if r.destination_id == job.destination_id and r.stream == job.stream
    }
    point = policy.checkpoint_at
    for start, end, backed_up in sorted(plan.ordered):
        if backed_up or rows.get(start) == BackupFileStatus.DONE:
            point = max(point, end) if point else end
        else:
            break
    else:
        # Everything found is done. Recordings still being written must not be passed over.
        horizon = plan.in_progress_from or (
            job.window_end - timedelta(seconds=get_settings().backup_inprogress_margin_seconds)
        )
        point = max(point, min(horizon, job.window_end)) if point else min(horizon, job.window_end)
    policy.checkpoint_at = point
