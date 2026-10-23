import logging
import uuid

from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models.enums import ConnectionType, NvrStatus
from app.models.nvr import Nvr
from app.services import nvr_ops
from app.services.nvr_ops import Operation
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="nvr.run_operation")
def run_operation(nvr_id: str, op: str) -> str:
    with get_sessionmaker()() as db:
        status = nvr_ops.execute(db, uuid.UUID(nvr_id), Operation(op))
    return status.value


@celery_app.task(name="nvr.check_all")
def check_all() -> int:
    """Periodic health check. Skips NVRs that were rejected for bad credentials: retrying a wrong password every few
    minutes would trip the NVR's account lockout. Editing the credentials resets the status and resumes checks."""
    queued = 0
    with get_sessionmaker()() as db:
        ids = list(
            db.scalars(
                select(Nvr.id).where(
                    Nvr.deleted_at.is_(None),
                    Nvr.enabled.is_(True),
                    Nvr.connection_type != ConnectionType.P2P,
                    Nvr.status.not_in([NvrStatus.AUTH_FAILED, NvrStatus.CONNECTING]),
                )
            )
        )
        for nvr_id in ids:
            if nvr_ops.claim(db, nvr_id) is not None:
                run_operation.delay(str(nvr_id), Operation.TEST.value)
                queued += 1
    return queued


@celery_app.task(name="tunnel.refresh_health")
def refresh_tunnel_health() -> int:
    from app.services import tunnel

    with get_sessionmaker()() as db:
        return tunnel.refresh_health(db)


@celery_app.task(name="streams.sweep")
def sweep_streams() -> int:
    from app.streaming import manager

    with get_sessionmaker()() as db:
        return manager.sweep_expired(db)


# Backup jobs can run for hours, so they have their own queue (start a second worker with `-Q backups`) and are not
# subject to the default 5 minute task limit. Otherwise a long download would block the NVR checks queued behind it.
BACKUP_TIME_LIMIT = 6 * 3600


@celery_app.task(name="backup.run_job", time_limit=BACKUP_TIME_LIMIT, soft_time_limit=BACKUP_TIME_LIMIT - 60)
def run_backup_job(job_id: str) -> str | None:
    from app.services import backup

    with get_sessionmaker()() as db:
        status = backup.run_job(db, uuid.UUID(job_id))
    return status.value if status else None


@celery_app.task(name="backup.schedule")
def schedule_backups() -> int:
    """Create a job for every backup policy that is due and queue it."""
    from app.services import backup

    with get_sessionmaker()() as db:
        ids = backup.schedule_due(db)
    for job_id in ids:
        run_backup_job.delay(str(job_id))
    return len(ids)


@celery_app.task(name="backup.sweep")
def sweep_backups() -> int:
    """Recover jobs whose worker died and re-queue jobs whose message was lost or is waiting for a busy NVR."""
    from app.services import backup

    with get_sessionmaker()() as db:
        ids = backup.recover_stale(db)
    for job_id in ids:
        run_backup_job.delay(str(job_id))
    return len(ids)
