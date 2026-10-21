"""NVR connection tests and camera discovery. Pure service code: Celery tasks and tests both call `execute`."""

import logging
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TypeVar

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.integrations.adapter_factory import get_adapter
from app.integrations.base import (
    CameraProbe,
    CapabilityResult,
    ChannelInfo,
    DeviceInfo,
    NvrAdapter,
    NvrAuthError,
    NvrError,
    NvrProtocolError,
    NvrUnreachableError,
    NvrUnsupportedError,
)
from app.integrations.hostguard import HostNotAllowedError
from app.models.camera import Camera, CameraStreamProfile
from app.models.enums import (
    CameraStatus,
    CapabilityName,
    CapabilityStatus,
    ConnectionType,
    NvrStatus,
)
from app.models.nvr import Nvr, NvrCapability

log = logging.getLogger(__name__)
T = TypeVar("T")

CLAIM_STALE_AFTER = timedelta(minutes=3)
PROBE_CONCURRENCY = 4  # per-NVR cap on simultaneous camera probes
ATTEMPTS = 2  # bounded retries for transient network failures
RETRY_DELAY_SECONDS = 1.0


class Operation(StrEnum):
    TEST = "test"  # connect, read device info, probe capabilities. Cameras untouched.
    DISCOVER = "discover"  # test + sync cameras and their stream profiles
    RECONNECT = "reconnect"  # same work as DISCOVER; named separately for the user-facing action


def utcnow() -> datetime:
    return datetime.now(UTC)


def claim(db: Session, nvr_id: uuid.UUID) -> NvrStatus | None:
    """Atomically take the single in-flight slot for this NVR.

    Returns the previous status when claimed (so a failed enqueue can restore it), or None when another operation
    is already running (a CONNECTING status older than CLAIM_STALE_AFTER is treated as a dead worker).
    """
    prev = db.scalar(select(Nvr.status).where(Nvr.id == nvr_id))
    now = utcnow()
    result = db.execute(
        update(Nvr)
        .where(
            Nvr.id == nvr_id,
            Nvr.deleted_at.is_(None),
            Nvr.enabled.is_(True),
            (Nvr.status != NvrStatus.CONNECTING) | (Nvr.status_changed_at < now - CLAIM_STALE_AFTER),
        )
        .values(status=NvrStatus.CONNECTING, status_message=None, status_changed_at=now)
    )
    db.commit()
    return prev if result.rowcount == 1 else None  # type: ignore[attr-defined]


def restore(db: Session, nvr_id: uuid.UUID, status: NvrStatus, message: str) -> None:
    db.execute(
        update(Nvr).where(Nvr.id == nvr_id).values(status=status, status_message=message, status_changed_at=utcnow())
    )
    db.commit()


def _with_retries[T](fn: Callable[[], T]) -> T:
    last: NvrUnreachableError | None = None
    for attempt in range(ATTEMPTS):
        try:
            return fn()
        except NvrUnreachableError as exc:
            last = exc
            if attempt + 1 < ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
    assert last is not None
    raise last


@dataclass
class Collected:
    info: DeviceInfo | None = None
    capabilities: list[CapabilityResult] = field(default_factory=list)
    channels: list[ChannelInfo] | None = None
    probes: dict[int, CameraProbe] = field(default_factory=dict)


def _collect(adapter: NvrAdapter, op: Operation) -> Collected:
    out = Collected()
    with adapter:
        out.info = adapter.get_device_info()
        if op in (Operation.DISCOVER, Operation.RECONNECT):
            out.channels = adapter.list_cameras()  # sets the channel the capability probes use
        out.capabilities = adapter.get_capabilities()
        if out.channels:
            with ThreadPoolExecutor(max_workers=PROBE_CONCURRENCY) as pool:
                for probe in pool.map(lambda c: adapter.get_camera_status(c.channel_number), out.channels):
                    out.probes[probe.channel_number] = probe
    return out


# --------------------------------------------------------------------------------------------------- persistence
def _save_capabilities(db: Session, nvr: Nvr, results: list[CapabilityResult], now: datetime) -> None:
    existing = {c.name: c for c in db.scalars(select(NvrCapability).where(NvrCapability.nvr_id == nvr.id))}
    for r in results:
        row = existing.get(r.name)
        if row is None:
            row = NvrCapability(nvr_id=nvr.id, name=r.name)
            db.add(row)
        row.status, row.detail, row.checked_at = r.status, r.detail[:512], now


def sync_cameras(
    db: Session, nvr: Nvr, channels: list[ChannelInfo], probes: dict[int, CameraProbe], now: datetime
) -> dict[str, int]:
    """Upsert by (nvr, channel). Never deletes: a camera that disappeared becomes UNAVAILABLE so its history and
    backup policy survive, and it returns to life if the channel reappears."""
    existing = {c.channel_number: c for c in db.scalars(select(Camera).where(Camera.nvr_id == nvr.id))}
    seen: set[int] = set()
    added = updated = 0
    for ch in channels:
        seen.add(ch.channel_number)
        cam = existing.get(ch.channel_number)
        if cam is None:
            cam = Camera(nvr_id=nvr.id, campus_id=nvr.campus_id, channel_number=ch.channel_number)
            db.add(cam)
            added += 1
        else:
            updated += 1
        cam.campus_id = nvr.campus_id
        cam.name, cam.model, cam.last_discovered_at = ch.name, ch.model, now
        probe = probes.get(ch.channel_number)
        if probe is not None:
            cam.status = CameraStatus.ONLINE if probe.online else CameraStatus.OFFLINE
            cam.status_detail, cam.status_checked_at = probe.detail[:256], now
        elif cam.status == CameraStatus.UNAVAILABLE:
            cam.status = CameraStatus.UNKNOWN
        db.flush()
        profiles = {
            p.stream: p for p in db.scalars(select(CameraStreamProfile).where(CameraStreamProfile.camera_id == cam.id))
        }
        for p in ch.profiles:
            row = profiles.get(p.stream)
            if row is None:
                row = CameraStreamProfile(camera_id=cam.id, stream=p.stream)
                db.add(row)
            row.codec, row.width, row.height, row.bitrate_kbps, row.fps = (
                p.codec,
                p.width,
                p.height,
                p.bitrate_kbps,
                p.fps,
            )
    gone = 0
    for number, cam in existing.items():
        if number not in seen and cam.status != CameraStatus.UNAVAILABLE:
            cam.status, cam.status_detail, cam.status_checked_at = (
                CameraStatus.UNAVAILABLE,
                "No longer reported by the NVR",
                now,
            )
            gone += 1
    return {"added": added, "updated": updated, "unavailable": gone}


def _status_from(caps: list[CapabilityResult], warnings: tuple[str, ...]) -> tuple[NvrStatus, str | None]:
    by = {c.name: c.status for c in caps}
    missing = [
        n.value
        for n in (CapabilityName.DEVICE_INFO, CapabilityName.CHANNEL_DISCOVERY)
        if by.get(n) != CapabilityStatus.SUPPORTED_TESTED
    ]
    notes = " ".join(warnings)
    if missing:
        return NvrStatus.PARTIALLY_SUPPORTED, f"Not available on this NVR: {', '.join(missing)}. {notes}".strip()
    return NvrStatus.CONNECTED, notes or None


# --------------------------------------------------------------------------------------------------- entry point
def execute(db: Session, nvr_id: uuid.UUID, op: Operation) -> NvrStatus:
    nvr = db.get(Nvr, nvr_id)
    if nvr is None or nvr.deleted_at is not None:
        return NvrStatus.OFFLINE
    now = utcnow()
    try:
        adapter = get_adapter(nvr)
        data = _with_retries(lambda: _collect(adapter, op))
    except NvrAuthError as exc:
        return _finish(
            db, nvr, NvrStatus.AUTH_FAILED, exc.message + " Automatic checks are paused until credentials are updated."
        )
    except NvrUnreachableError as exc:
        return _finish(db, nvr, NvrStatus.OFFLINE, exc.message)
    except NvrProtocolError as exc:
        return _finish(db, nvr, NvrStatus.UNSUPPORTED, exc.message)
    except HostNotAllowedError as exc:
        return _finish(db, nvr, NvrStatus.UNSUPPORTED, exc.message)
    except NvrUnsupportedError as exc:
        status = (
            NvrStatus.P2P_UNAVAILABLE if nvr.connection_type == ConnectionType.P2P else NvrStatus.PARTIALLY_SUPPORTED
        )
        return _finish(db, nvr, status, exc.message)
    except NvrError as exc:
        return _finish(db, nvr, NvrStatus.OFFLINE, exc.message)
    except Exception:  # noqa: BLE001 - a worker must always leave the NVR in a resolved state
        log.exception("Unexpected error while checking NVR %s", nvr_id)
        return _finish(db, nvr, NvrStatus.OFFLINE, "Unexpected error during the check. See the server log.")

    assert data.info is not None
    nvr.model, nvr.firmware = data.info.model, data.info.firmware
    nvr.timezone_code, nvr.utc_offset_minutes = data.info.timezone_code, data.info.utc_offset_minutes
    _save_capabilities(db, nvr, data.capabilities, now)
    if data.channels is not None:
        sync_cameras(db, nvr, data.channels, data.probes, now)
    status, message = _status_from(data.capabilities, data.info.warnings)
    nvr.last_success_at = now
    return _finish(db, nvr, status, message)


def _finish(db: Session, nvr: Nvr, status: NvrStatus, message: str | None) -> NvrStatus:
    now = utcnow()
    nvr.status, nvr.status_message = status, (message or None) and message[:512]
    nvr.status_changed_at, nvr.last_checked_at = now, now
    db.commit()
    return status
