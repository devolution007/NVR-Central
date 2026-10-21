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
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
