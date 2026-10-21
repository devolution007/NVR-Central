import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core import security
from app.core.config import Settings
from app.models import Camera, CameraBackupPolicy, Nvr
from app.models.enums import BackupStream, ConnectionType, NvrStatus, RecordingMode
from tests.conftest import ROOT, TEST_URL, make_campus


def test_password_hash_roundtrip_and_policy() -> None:
    h = security.hash_password("a-long-enough-password")
    assert h != "a-long-enough-password" and h.startswith("$argon2")
    assert security.verify_password("a-long-enough-password", h)
    assert not security.verify_password("another-password-here", h)
    assert not security.verify_password("x", "not-a-hash")
    with pytest.raises(security.PasswordPolicyError):
        security.validate_password_policy("short")
    with pytest.raises(security.PasswordPolicyError):
        security.validate_password_policy("SameAsUsername1", "sameasusername1")


def test_secret_encryption_roundtrip_is_randomised_and_masks() -> None:
    a, b = security.encrypt_secret("hunter2"), security.encrypt_secret("hunter2")
    assert a != b and "hunter2" not in a
    assert security.decrypt_secret(a) == "hunter2"
    assert security.mask_secret("ABCD1234EFGH5678") == "***5678"
    assert security.mask_secret("abc") == "***"
    with pytest.raises(ValueError):
        security.decrypt_secret("garbage")


def test_refresh_tokens_are_stored_hashed() -> None:
    t = security.new_opaque_token()
    assert security.hash_token(t) != t and len(security.hash_token(t)) == 64


def test_settings_reject_weak_or_inconsistent_config() -> None:
    good_key = os.environ["ENCRYPTION_KEY"]
    base = {"database_url": "postgresql+psycopg://x/y", "secret_key": "s" * 40, "encryption_key": good_key}
    Settings(**base)
    with pytest.raises(ValidationError):
        Settings(**{**base, "secret_key": "short"})
    with pytest.raises(ValidationError):
        Settings(**{**base, "encryption_key": "not-a-fernet-key"})
    with pytest.raises(ValidationError):
        Settings(**{**base, "environment": "production", "cookie_secure": False})
    Settings(**{**base, "environment": "production", "cookie_secure": True})


def test_backup_policy_defaults_to_substream_and_disabled(db) -> None:
    """Decision 2026-10-21: backup stream is selectable per camera and defaults to the substream."""
    campus = make_campus(db, "C")
    nvr = Nvr(
        campus_id=campus.id,
        name="N",
        connection_type=ConnectionType.LAN,
        host="10.0.0.1",
        username="u",
        password_encrypted=security.encrypt_secret("p"),
    )
    db.add(nvr)
    db.flush()
    cam = Camera(nvr_id=nvr.id, campus_id=campus.id, channel_number=1, name="Cam 1")
    db.add(cam)
    db.flush()
    policy = CameraBackupPolicy(camera_id=cam.id)
    db.add(policy)
    db.commit()
    db.refresh(policy)
    assert policy.backup_stream == BackupStream.SUB
    assert policy.enabled is False
    assert policy.recording_mode == RecordingMode.ALL
    assert nvr.status == NvrStatus.PENDING
    policy.backup_stream = BackupStream.MAIN
    db.commit()
    db.refresh(policy)
    assert policy.backup_stream == BackupStream.MAIN


def test_one_camera_per_channel_and_one_policy_per_camera(db) -> None:
    campus = make_campus(db, "C")
    nvr = Nvr(
        campus_id=campus.id,
        name="N",
        connection_type=ConnectionType.LAN,
        host="10.0.0.1",
        username="u",
        password_encrypted="x",
    )
    db.add(nvr)
    db.flush()
    cam = Camera(nvr_id=nvr.id, campus_id=campus.id, channel_number=1)
    db.add(cam)
    db.flush()
    db.add(CameraBackupPolicy(camera_id=cam.id))
    db.commit()
    db.add(Camera(nvr_id=nvr.id, campus_id=campus.id, channel_number=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(CameraBackupPolicy(camera_id=cam.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_timestamps_are_timezone_aware_utc(db) -> None:
    campus = make_campus(db, "C")
    db.refresh(campus)
    assert campus.created_at.tzinfo is not None
    assert campus.created_at.utcoffset().total_seconds() == 0


def test_migrations_match_models_and_are_reversible() -> None:
    """Fresh database -> upgrade head -> no drift against the models -> downgrade -> upgrade again."""
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext

    from alembic import command
    from app.db.base import Base

    name = f"{TEST_URL.database}_mig_{uuid.uuid4().hex[:6]}"
    admin = create_engine(TEST_URL.set(database=TEST_URL.database.removesuffix("_test")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_URL.set(database=name)
    try:
        cfg = Config(str(ROOT / "backend" / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
        import argparse

        cfg.cmd_opts = argparse.Namespace(x=[f"db_url={url.render_as_string(hide_password=False)}"])
        command.upgrade(cfg, "head")
        engine = create_engine(url)
        with engine.connect() as conn:
            drift = compare_metadata(MigrationContext.configure(conn), Base.metadata)
            assert drift == [], drift
            tables = set(inspect(conn).get_table_names())
        assert {
            "users",
            "campuses",
            "nvrs",
            "cameras",
            "camera_backup_policies",
            "audit_logs",
            "refresh_sessions",
            "user_campus_permissions",
        } <= tables
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_create_admin_cli_rejects_weak_password_and_creates_user(db) -> None:
    env = {**os.environ, "NVR_ADMIN_PASSWORD": "short"}
    backend = Path(ROOT) / "backend"
    weak = subprocess.run(
        [sys.executable, "-m", "app.cli", "create-admin", "--username", "boss"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
    )
    assert weak.returncode == 1 and "at least" in weak.stderr
    env["NVR_ADMIN_PASSWORD"] = "a-long-enough-password"
    ok = subprocess.run(
        [sys.executable, "-m", "app.cli", "create-admin", "--username", "boss"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stderr
    again = subprocess.run(
        [sys.executable, "-m", "app.cli", "create-admin", "--username", "BOSS"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
    )
    assert again.returncode == 1 and "already exists" in again.stderr
