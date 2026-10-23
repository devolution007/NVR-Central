from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    environment: str = "development"
    database_url: str
    secret_key: str
    encryption_key: str

    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173"

    redis_url: str = "redis://127.0.0.1:16379/0"
    # Loopback NVR addresses are refused by default (they would point at this server). Only tests turn this on.
    allow_loopback_nvr_hosts: bool = False
    # Tests run Celery tasks inline in the calling thread. Never enable in a real deployment.
    celery_task_always_eager: bool = False

    # Live view (MediaMTX). The backend is the only client of both URLs; browsers never reach MediaMTX directly.
    mediamtx_api_url: str = "http://127.0.0.1:9997"
    mediamtx_hls_url: str = "http://127.0.0.1:8888"
    stream_session_ttl_seconds: int = 60  # a viewer must send a keepalive within this time or the session expires
    # Concurrent upstream streams per NVR. 8 simultaneous substreams were measured on the test NVR; 16 was not tested.
    max_streams_per_nvr: int = 8
    max_streams_total: int = 40
    # auto: convert to H.264 unless the camera already sends H.264. always / never override.
    stream_transcode: str = "auto"

    # Backups. Recordings are streamed to this folder, uploaded, then deleted. Put it on a disk with room for one
    # recording per running job (main stream: about 1 GB per camera-hour).
    backup_staging_dir: str = "../data/backup-staging"
    backup_min_free_gb: float = 5.0  # a download is refused if it would leave less than this free
    backup_overlap_minutes: int = 10  # each run starts this long before the checkpoint; dedup makes the overlap free
    backup_inprogress_margin_seconds: int = 120  # a recording that ended more recently than this may still be written
    backup_max_attempts: int = 5  # per recording file, across runs, before it is left failed for a manual retry
    backup_job_stale_seconds: int = 300  # a running job with no heartbeat for this long is recovered

    # WireGuard hub (campus connectivity). All optional: without a hub key the Connectivity page says so.
    wg_hub_private_key: str = ""
    wg_hub_endpoint: str = ""
    wg_interface: str = "wg0"
    wg_hub_conf_path: str = ""

    login_max_failures: int = 5
    login_lock_minutes: int = 15
    login_ip_limit_per_minute: int = 20

    @field_validator("secret_key")
    @classmethod
    def _secret_long_enough(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters (run scripts/init-env.py)")
        return v

    @field_validator("encryption_key")
    @classmethod
    def _valid_fernet_key(cls, v: str) -> str:
        try:
            Fernet(v.encode())
        except Exception as exc:
            raise ValueError("ENCRYPTION_KEY is not a valid Fernet key (run scripts/init-env.py)") from exc
        return v

    @model_validator(mode="after")
    def _production_requires_secure_cookies(self) -> "Settings":
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when ENVIRONMENT=production")
        if self.environment == "production" and self.allow_loopback_nvr_hosts:
            raise ValueError("ALLOW_LOOPBACK_NVR_HOSTS must be false when ENVIRONMENT=production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
