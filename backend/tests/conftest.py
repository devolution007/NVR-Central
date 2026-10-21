"""Test harness. Runs against a real PostgreSQL database named <db>_test (created on demand).

Start the dev database first:  docker compose up -d db
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def _read_dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


_dot = _read_dotenv()
_base_url = make_url(os.environ.get("DATABASE_URL") or _dot["DATABASE_URL"])
TEST_DB = f"{_base_url.database}_test"
TEST_URL = _base_url.set(database=TEST_DB)

os.environ["DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)
os.environ.setdefault("SECRET_KEY", _dot.get("SECRET_KEY", "x" * 48))
os.environ.setdefault("ENCRYPTION_KEY", _dot["ENCRYPTION_KEY"])
os.environ["COOKIE_SECURE"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["LOGIN_MAX_FAILURES"] = "5"
os.environ["LOGIN_IP_LIMIT_PER_MINUTE"] = "20"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"  # run tasks inline; tests never need a worker process
os.environ["ALLOW_LOOPBACK_NVR_HOSTS"] = "true"  # the simulated NVR listens on 127.0.0.1
os.environ["WG_HUB_ENDPOINT"] = "vpn.test.example:51820"
os.environ["WG_HUB_CONF_PATH"] = ""
# A throwaway hub key, so tests never depend on or expose the developer's real one.
import base64 as _b64  # noqa: E402

os.environ["WG_HUB_PRIVATE_KEY"] = _b64.b64encode(os.urandom(32)).decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Campus, User, UserCampusPermission  # noqa: E402
from app.models.enums import Role  # noqa: E402
from app.services.ratelimit import login_limiter  # noqa: E402

PASSWORD = "correct-horse-battery"


def _ensure_database(url: Any, name: str, *, recreate: bool = False) -> None:
    admin = create_engine(url.set(database=_base_url.database), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if recreate:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        exists = conn.scalar(text("select 1 from pg_database where datname = :n"), {"n": name})
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    from alembic.config import Config

    from alembic import command

    _ensure_database(TEST_URL, TEST_DB, recreate=True)
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    command.upgrade(cfg, "head")  # also proves the migrations apply to an empty database
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    with get_engine().begin() as conn:
        tables = conn.scalars(
            text("select tablename from pg_tables where schemaname='public' and tablename <> 'alembic_version'")
        ).all()
        conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    login_limiter.reset()
    yield


class Api:
    """TestClient wrapper that behaves like the SPA: echoes the CSRF cookie in X-CSRF-Token."""

    def __init__(self) -> None:
        self.client = TestClient(app)

    def login(self, username: str, password: str = PASSWORD) -> Any:
        return self.client.post("/api/v1/auth/login", json={"username": username, "password": password})

    def _headers(self) -> dict[str, str]:
        token = self.client.cookies.get("csrf_token")
        return {"X-CSRF-Token": token} if token else {}

    def get(self, url: str, **kw: Any) -> Any:
        return self.client.get(url, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self.client.post(url, headers=self._headers(), **kw)

    def put(self, url: str, **kw: Any) -> Any:
        return self.client.put(url, headers=self._headers(), **kw)

    def patch(self, url: str, **kw: Any) -> Any:
        return self.client.patch(url, headers=self._headers(), **kw)

    def delete(self, url: str, **kw: Any) -> Any:
        return self.client.delete(url, headers=self._headers(), **kw)


@pytest.fixture
def api() -> Api:
    return Api()


@pytest.fixture
def db() -> Iterator[Any]:
    with get_sessionmaker()() as session:
        yield session


def make_user(
    db: Any, username: str, role: Role = Role.VIEWER, campuses: list[uuid.UUID] | None = None, active: bool = True
) -> User:
    user = User(
        username=username, display_name=username, password_hash=hash_password(PASSWORD), role=role, is_active=active
    )
    db.add(user)
    db.flush()
    for cid in campuses or []:
        db.add(UserCampusPermission(user_id=user.id, campus_id=cid))
    db.commit()
    return user


def make_campus(db: Any, name: str) -> Campus:
    campus = Campus(name=name)
    db.add(campus)
    db.commit()
    return campus


@pytest.fixture
def admin(db: Any) -> User:
    return make_user(db, "admin", Role.ADMIN)


@pytest.fixture
def admin_api(api: Api, admin: User) -> Api:
    assert api.login("admin").status_code == 200
    return api


@pytest.fixture(autouse=True)
def _block_real_devices() -> Iterator[None]:
    """Safety net: tests may talk to the loopback simulator, never to a real address (see NetworkBlockedAdapter)."""
    from app.integrations.adapter_factory import override_adapter
    from app.integrations.dahua.direct import DahuaDirectAdapter
    from tests.support.scripted import NetworkBlockedAdapter

    def guard(nvr: Any, params: Any) -> Any:
        if params.host.startswith("127."):
            return DahuaDirectAdapter(params)
        return NetworkBlockedAdapter(params.host)

    with override_adapter(guard):
        yield
