"""Connection test / discovery workflow, driven by a SIMULATED scripted adapter."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import encrypt_secret
from app.integrations.adapter_factory import override_adapter
from app.integrations.base import NvrAuthError, NvrProtocolError, NvrUnreachableError, NvrUnsupportedError
from app.models import Camera, CameraBackupPolicy, CameraStreamProfile, Nvr, NvrCapability
from app.models.enums import CameraStatus, CapabilityName, CapabilityStatus, ConnectionType, NvrStatus
from app.services import nvr_ops
from app.services.nvr_ops import Operation
from app.workers import tasks
from tests.conftest import make_campus
from tests.support.scripted import ScriptedAdapter, all_caps, builder_for, channel

SECRET = "S3cret-NVR-pass!"


@pytest.fixture
def adapter():
    a = ScriptedAdapter()
    with override_adapter(builder_for(a)):
        yield a


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(nvr_ops, "RETRY_DELAY_SECONDS", 0)


def make_nvr(db: Any, campus: Any = None, **kw: Any) -> Nvr:
    campus = campus or make_campus(db, f"C-{uuid.uuid4().hex[:6]}")
    nvr = Nvr(
        campus_id=campus.id, name=kw.pop("name", "NVR-1"), connection_type=kw.pop("connection_type", ConnectionType.LAN),
        host="10.0.0.5", username="admin", password_encrypted=encrypt_secret(SECRET),
        status=kw.pop("status", NvrStatus.PENDING), **kw,
    )  # fmt: skip
    db.add(nvr)
    db.commit()
    return nvr


def cams(db: Any, nvr: Nvr) -> dict[int, Camera]:
    db.expire_all()
    return {c.channel_number: c for c in db.scalars(select(Camera).where(Camera.nvr_id == nvr.id))}


def test_discover_stores_device_cameras_profiles_and_capabilities(db, adapter) -> None:
    nvr = make_nvr(db)
    assert nvr_ops.execute(db, nvr.id, Operation.DISCOVER) == NvrStatus.CONNECTED
    db.refresh(nvr)
    assert (nvr.model, nvr.firmware, nvr.timezone_code, nvr.utc_offset_minutes) == ("DHI-SIM-NVR", "1.0", 7, 300)
    assert nvr.last_success_at is not None and nvr.last_checked_at is not None and nvr.status_message is None

    c = cams(db, nvr)
    assert sorted(c) == [1, 2] and c[1].status == CameraStatus.ONLINE and c[1].campus_id == nvr.campus_id
    profiles = list(db.scalars(select(CameraStreamProfile).where(CameraStreamProfile.camera_id == c[1].id)))
    assert {p.stream.value for p in profiles} == {"main", "sub"}
    caps = {x.name: x.status for x in db.scalars(select(NvrCapability).where(NvrCapability.nvr_id == nvr.id))}
    assert (
        caps[CapabilityName.RECORDING_DOWNLOAD] == CapabilityStatus.UNKNOWN
    )  # connecting never implies downloads work
    assert caps[CapabilityName.LIVE_VIDEO] == CapabilityStatus.SUPPORTED_TESTED


def test_resync_is_idempotent_keeps_ids_and_preserves_backup_policy(db, adapter) -> None:
    nvr = make_nvr(db)
    nvr_ops.execute(db, nvr.id, Operation.DISCOVER)
    first = cams(db, nvr)
    db.add(CameraBackupPolicy(camera_id=first[1].id, enabled=True))
    db.commit()

    adapter.channels = [channel(1, "Renamed lobby"), channel(2)]
    nvr_ops.execute(db, nvr.id, Operation.RECONNECT)
    nvr_ops.execute(db, nvr.id, Operation.DISCOVER)
    second = cams(db, nvr)
    assert len(second) == 2  # no duplicates after repeated syncs
    assert second[1].id == first[1].id and second[1].name == "Renamed lobby"
    policy = db.scalar(select(CameraBackupPolicy).where(CameraBackupPolicy.camera_id == first[1].id))
    assert policy is not None and policy.enabled is True and policy.backup_stream.value == "sub"
    assert (
        db.scalar(select(CameraStreamProfile).where(CameraStreamProfile.camera_id == second[1].id).limit(1)) is not None
    )
    assert len(list(db.scalars(select(CameraStreamProfile)))) == 4  # profiles updated in place, not multiplied


def test_vanished_camera_becomes_unavailable_not_deleted_and_can_return(db, adapter) -> None:
    nvr = make_nvr(db)
    nvr_ops.execute(db, nvr.id, Operation.DISCOVER)
    cam2_id = cams(db, nvr)[2].id

    adapter.channels = [channel(1)]
    nvr_ops.execute(db, nvr.id, Operation.DISCOVER)
    gone = cams(db, nvr)[2]
    assert gone.id == cam2_id and gone.status == CameraStatus.UNAVAILABLE

    adapter.channels = [channel(1), channel(2)]
    nvr_ops.execute(db, nvr.id, Operation.DISCOVER)
    back = cams(db, nvr)[2]
    assert back.id == cam2_id and back.status == CameraStatus.ONLINE


def test_plain_test_leaves_camera_inventory_alone(db, adapter) -> None:
    nvr = make_nvr(db)
    nvr_ops.execute(db, nvr.id, Operation.DISCOVER)
    adapter.channels = []
    nvr_ops.execute(db, nvr.id, Operation.TEST)
    assert adapter.listed == 1
    assert all(c.status != CameraStatus.UNAVAILABLE for c in cams(db, nvr).values())


def test_unresponsive_camera_is_offline_with_a_reason(db, adapter) -> None:
    adapter.offline_channels = {2}
    nvr = make_nvr(db)
    nvr_ops.execute(db, nvr.id, Operation.DISCOVER)
    c = cams(db, nvr)
    assert (c[1].status, c[2].status) == (CameraStatus.ONLINE, CameraStatus.OFFLINE)
    assert c[2].status_detail == "scripted offline" and c[2].status_checked_at is not None


def test_auth_failure_pauses_checks_and_never_leaks_the_password(db, adapter) -> None:
    adapter.raise_on_connect = NvrAuthError("The NVR rejected the username or password (or the account is locked).")
    nvr = make_nvr(db)
    assert nvr_ops.execute(db, nvr.id, Operation.RECONNECT) == NvrStatus.AUTH_FAILED
    db.refresh(nvr)
    assert "paused" in (nvr.status_message or "") and SECRET not in (nvr.status_message or "")
    assert adapter.connect_calls == 1  # an auth failure is not retried: that is how accounts get locked


def test_unreachable_nvr_is_offline_after_bounded_retries(db, adapter) -> None:
    adapter.raise_on_connect = NvrUnreachableError("Connection timed out. The NVR did not answer.")
    nvr = make_nvr(db)
    assert nvr_ops.execute(db, nvr.id, Operation.TEST) == NvrStatus.OFFLINE
    assert adapter.connect_calls == nvr_ops.ATTEMPTS


def test_non_dahua_device_is_unsupported(db, adapter) -> None:
    adapter.raise_on_connect = NvrProtocolError("The device answered but does not look like a Dahua NVR")
    nvr = make_nvr(db)
    assert nvr_ops.execute(db, nvr.id, Operation.TEST) == NvrStatus.UNSUPPORTED


def test_unexpected_crash_still_resolves_the_status_without_exposing_details(db, adapter) -> None:
    adapter.raise_on_connect = RuntimeError(f"boom password={SECRET}")
    nvr = make_nvr(db)
    assert nvr_ops.execute(db, nvr.id, Operation.TEST) == NvrStatus.OFFLINE
    db.refresh(nvr)
    assert SECRET not in (nvr.status_message or "") and nvr.status != NvrStatus.CONNECTING


def test_missing_channel_discovery_means_partially_supported(db, adapter) -> None:
    adapter.capabilities = all_caps(channel_discovery=CapabilityStatus.UNSUPPORTED)
    nvr = make_nvr(db)
    assert nvr_ops.execute(db, nvr.id, Operation.TEST) == NvrStatus.PARTIALLY_SUPPORTED
    db.refresh(nvr)
    assert "channel_discovery" in (nvr.status_message or "")


def test_clock_warning_is_surfaced_on_a_connected_nvr(db, adapter) -> None:
    from dataclasses import replace

    adapter.info = replace(
        adapter.info, warnings=("NVR clock suggests UTC+1.00 but its timezone setting says UTC+5.00.",)
    )
    nvr = make_nvr(db)
    assert nvr_ops.execute(db, nvr.id, Operation.TEST) == NvrStatus.CONNECTED
    db.refresh(nvr)
    assert "clock" in (nvr.status_message or "").lower()


def test_p2p_nvr_reports_p2p_unavailable_and_never_touches_the_network(db) -> None:
    nvr = make_nvr(db, connection_type=ConnectionType.P2P, p2p_serial_encrypted=encrypt_secret("ABCD1234"))
    assert nvr_ops.execute(db, nvr.id, Operation.RECONNECT) == NvrStatus.P2P_UNAVAILABLE
    db.refresh(nvr)
    assert "disabled" in (nvr.status_message or "")


def test_unsupported_operation_on_direct_nvr_is_partial_not_offline(db, adapter) -> None:
    adapter.raise_on_connect = NvrUnsupportedError("nope")
    nvr = make_nvr(db)
    assert nvr_ops.execute(db, nvr.id, Operation.TEST) == NvrStatus.PARTIALLY_SUPPORTED


def test_claim_allows_one_operation_at_a_time_and_recovers_from_dead_workers(db) -> None:
    nvr = make_nvr(db)
    assert nvr_ops.claim(db, nvr.id) == NvrStatus.PENDING
    assert nvr_ops.claim(db, nvr.id) is None  # already running
    db.expire_all()
    row = db.get(Nvr, nvr.id)
    row.status_changed_at = row.status_changed_at - nvr_ops.CLAIM_STALE_AFTER - timedelta(seconds=1)
    db.commit()
    assert nvr_ops.claim(db, nvr.id) == NvrStatus.CONNECTING  # stale claim from a crashed worker is retaken


def test_claim_refuses_disabled_and_deleted_nvrs(db) -> None:
    disabled = make_nvr(db, name="off", enabled=False)
    assert nvr_ops.claim(db, disabled.id) is None
    from app.db.base import utcnow

    gone = make_nvr(db, name="gone", deleted_at=utcnow())
    assert nvr_ops.claim(db, gone.id) is None


def test_periodic_check_skips_rejected_credentials_p2p_disabled_and_busy_nvrs(db, adapter) -> None:
    campus = make_campus(db, "Periodic")
    ok = make_nvr(db, campus, name="ok", status=NvrStatus.OFFLINE)
    make_nvr(db, campus, name="auth", status=NvrStatus.AUTH_FAILED)
    make_nvr(db, campus, name="busy", status=NvrStatus.CONNECTING)
    make_nvr(db, campus, name="off", enabled=False)
    make_nvr(
        db, campus, name="p2p", connection_type=ConnectionType.P2P, p2p_serial_encrypted=encrypt_secret("ABCD1234")
    )
    assert tasks.check_all() == 1
    db.expire_all()
    assert db.get(Nvr, ok.id).status == NvrStatus.CONNECTED
    assert db.scalar(select(Nvr.status).where(Nvr.name == "auth")) == NvrStatus.AUTH_FAILED
