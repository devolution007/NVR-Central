"""Queue an NVR operation. Kept separate from the Celery app so the API does not import worker internals."""

import uuid

from app.services.nvr_ops import Operation


class QueueUnavailableError(Exception):
    pass


def enqueue(nvr_id: uuid.UUID, op: Operation) -> None:
    from app.workers.tasks import run_operation

    try:
        run_operation.delay(str(nvr_id), op.value)
    except Exception as exc:  # broker down, connection refused, timeout
        raise QueueUnavailableError("The background worker queue is unavailable") from exc


def enqueue_backup(job_id: uuid.UUID) -> None:
    from app.workers.tasks import run_backup_job

    try:
        run_backup_job.delay(str(job_id))
    except Exception as exc:
        raise QueueUnavailableError("The background worker queue is unavailable") from exc
