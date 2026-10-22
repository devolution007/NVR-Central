from celery import Celery

from app.core.config import get_settings

_s = get_settings()

celery_app = Celery("nvr_central", broker=_s.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_always_eager=_s.celery_task_always_eager,
    task_eager_propagates=False,
    task_acks_late=True,  # a crashed worker's task is redelivered instead of lost
    worker_prefetch_multiplier=1,
    task_soft_time_limit=240,
    task_time_limit=300,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3},
    timezone="UTC",
    beat_schedule={
        "nvr-health-check": {"task": "nvr.check_all", "schedule": 300.0},
        "tunnel-health": {"task": "tunnel.refresh_health", "schedule": 60.0},
        "stream-sweep": {"task": "streams.sweep", "schedule": 30.0},
    },
)
