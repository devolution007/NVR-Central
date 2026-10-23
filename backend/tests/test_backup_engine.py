"""Backup engine against a SIMULATED Dahua NVR and a SIMULATED SFTP server (both real network protocols, local)."""

import hashlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import encrypt_secret
from app.db.base import utcnow
from app.models.backup import BackupFile, BackupJob, SftpDestination
from app.models.camera import Camera, CameraBackupPolicy
from app.models.enums import (
    BackupFileStatus,
    BackupJobStatus,
    BackupStream,
    BackupTrigger,
    ConnectionType,
    NvrStatus,
    SftpAuthType,
)
from app.models.nvr import Nvr
from app.services import backup
from tests.conftest import make_campus
from tests.support.fake_dahua import FakeDahuaServer, SimNvr, SimRecording
from tests.support.fake_sftp import PASSWORD, USER, FakeSftpServer

OFFSET = 300  # the simulated NVR clock is UTC+5


@dataclass
class Env:
    nvr_sim: SimNvr
    nvr_server: FakeDahuaServer
    sftp: FakeSftpServer
    nvr: Nvr
    camera: Camera
    dest: SftpDestination
    policy: CameraBackupPolicy
    remote: Path

    def local_now(self) -> datetime:
        return datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=OFFSET)

    def add_recording(self, hours_ago: int, stream: str = "Extra1", size: int = 300_000, **kw: Any) -> SimRecording:
        """A finished hourly recording that started `hours_ago` whole hours before the current NVR hour."""
        start = self.local_now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours_ago)
        rec = SimRecording(1, stream, start, start + timedelta(hours=1), data=os.urandom(size), **kw)
        self.nvr_sim.recordings.append(rec)
        return rec

    def add_in_progress(self, stream: str = "Extra1") -> SimRecording:
        now = self.local_now()
        rec = SimRecording(
            1, stream, now.replace(minute=0, second=0, microsecond=0), now - timedelta(seconds=5), data=os.urandom(1000)
        )
        self.nvr_sim.recordings.append(rec)
        return rec

    def job(self, hours: int = 12, trigger: BackupTrigger = BackupTrigger.MANUAL, db: Any = None) -> BackupJob:
        now = utcnow()
        job = backup.create_job(
            db, self.camera, self.nvr, self.dest, BackupStream.SUB, trigger, now - timedelta(hours=hours), now
        )
        db.commit()
        return job

    def remote_files(self) -> list[str]:
        return sorted(str(p.relative_to(self.remote)).replace("\\", "/") for p in self.remote.rglob("*") if p.is_file())


@pytest.fixture
def env(db: Any, tmp_path: Path) -> Iterator[Env]:
    sim = SimNvr()
    server = FakeDahuaServer(sim).start()
    remote = tmp_path / "remote"
    remote.mkdir()
    sftp = FakeSftpServer(remote).start()
    campus = make_campus(db, "North Campus")
    nvr = Nvr(
        campus_id=campus.id, name="Main NVR", connection_type=ConnectionType.LAN, host="127.0.0.1",
        http_port=server.http_port, rtsp_port=server.rtsp_port, username="admin",
        password_encrypted=encrypt_secret("secret"), status=NvrStatus.CONNECTED, utc_offset_minutes=OFFSET,
        last_success_at=utcnow(),
    )  # fmt: skip
    db.add(nvr)
    db.flush()
    camera = Camera(nvr_id=nvr.id, campus_id=campus.id, channel_number=1, name="Lobby Cam")
    dest = SftpDestination(
        name="Archive", host="127.0.0.1", port=sftp.port, username=USER, auth_type=SftpAuthType.PASSWORD,
        password_encrypted=encrypt_secret(PASSWORD), base_path="/backups", host_key_sha256=sftp.fingerprint,
    )  # fmt: skip
    db.add_all([camera, dest])
    db.flush()
    policy = CameraBackupPolicy(camera_id=camera.id, enabled=True, destination_id=dest.id, frequency_minutes=60)
    db.add(policy)
    db.commit()
    yield Env(sim, server, sftp, nvr, camera, dest, policy, remote)
    server.stop()
    sftp.stop()


def files(db: Any, job: BackupJob | None = None) -> list[BackupFile]:
    q = select(BackupFile).order_by(BackupFile.recording_start)
    if job is not None:
        q = q.where(BackupFile.job_id == job.id)
    db.expire_all()
    return list(db.scalars(q))


def test_backs_up_finished_recordings_and_leaves_the_one_still_being_written(db: Any, env: Env) -> None:
    r1, r2 = env.add_recording(3), env.add_recording(2)
    env.add_in_progress()
    job = env.job(db=db)

    assert backup.run_job(db, job.id) == BackupJobStatus.SUCCESS
    db.expire_all()
    job = db.get(BackupJob, job.id)
    assert (job.files_total, job.files_done, job.files_failed, job.files_skipped) == (2, 2, 0, 1)
    assert job.bytes_done == len(r1.data) + len(r2.data) and job.finished_at is not None
    rows = files(db)
    assert [f.status for f in rows] == [BackupFileStatus.DONE, BackupFileStatus.DONE]
    assert rows[0].sha256 == hashlib.sha256(r1.data).hexdigest()

    start = rows[0].recording_start
    expected_dir = f"backups/North-Campus/Main-NVR/ch01-Lobby-Cam/sub/{start:%Y}/{start:%m}/{start:%d}"
    name = f"{start:%Y%m%dT%H%M%SZ}_{(start + timedelta(hours=1)):%Y%m%dT%H%M%SZ}.dav"
    assert f"{expected_dir}/{name}" in env.remote_files() and f"{expected_dir}/{name}.sha256" in env.remote_files()
    assert (env.remote / expected_dir / name).read_bytes() == r1.data
    assert not [p for p in env.remote_files() if p.endswith(".part")]
    assert not list(backup._staging_root().rglob("*.dav"))  # staging cleaned up


def test_nvr_local_time_is_converted_to_utc_with_the_nvrs_own_offset(db: Any, env: Env) -> None:
    rec = env.add_recording(2)
    job = env.job(db=db)
    backup.run_job(db, job.id)
    row = files(db)[0]
    assert row.recording_start == (rec.start - timedelta(minutes=OFFSET)).replace(tzinfo=UTC)


def test_running_again_uploads_nothing_twice(db: Any, env: Env) -> None:
    env.add_recording(3)
    env.add_recording(2)
    first = env.job(db=db)
    backup.run_job(db, first.id)
    uploaded = env.remote_files()
    downloads_before = len([p for p in env.nvr_sim.requests if "RPC_Loadfile" in p])
    second = env.job(db=db)
    assert backup.run_job(db, second.id) == BackupJobStatus.SUCCESS
    assert env.remote_files() == uploaded and len(files(db)) == 2
    assert len([p for p in env.nvr_sim.requests if "RPC_Loadfile" in p]) == downloads_before  # not even downloaded


def test_only_the_selected_stream_is_backed_up(db: Any, env: Env) -> None:
    env.add_recording(3, stream="Main")
    env.add_recording(3, stream="Extra1")
    job = env.job(db=db)
    backup.run_job(db, job.id)
    assert len(files(db)) == 1 and "/sub/" in env.remote_files()[0]


def test_connection_lost_during_download_leaves_no_partial_file_and_the_next_run_completes_it(
    db: Any, env: Env
) -> None:
    rec = env.add_recording(2, size=700_000)
    env.nvr_sim.download_cut_after = 100_000
    job = env.job(db=db)
    assert backup.run_job(db, job.id) == BackupJobStatus.FAILED
    db.expire_all()
    assert "unreachable" in db.get(BackupJob, job.id).message
    row = files(db)[0]
    assert row.status == BackupFileStatus.PENDING and row.attempts == 1  # will be retried, not lost
    assert env.remote_files() == [] and not list(backup._staging_root().rglob("*.dav"))

    again = env.job(db=db)
    assert backup.run_job(db, again.id) == BackupJobStatus.SUCCESS
    assert files(db)[0].status == BackupFileStatus.DONE and files(db)[0].attempts == 2
    assert (env.remote / env.remote_files()[0]).read_bytes() == rec.data


def test_sftp_failure_mid_upload_marks_the_file_failed_and_leaves_nothing_behind(db: Any, env: Env) -> None:
    env.add_recording(3, size=900_000)
    env.add_recording(2, size=1000)
    env.sftp.fail_after_bytes = 200_000
    job = env.job(db=db)
    assert backup.run_job(db, job.id) == BackupJobStatus.PARTIAL  # the second file still went through
    rows = files(db)
    assert [f.status for f in rows] == [BackupFileStatus.FAILED, BackupFileStatus.DONE]
    assert rows[0].error and not [p for p in env.remote_files() if ".part" in p]
    db.expire_all()
    assert "1 of 2 recordings failed" in db.get(BackupJob, job.id).message

    retry = env.job(db=db)
    assert backup.run_job(db, retry.id) == BackupJobStatus.SUCCESS
    assert [f.status for f in files(db)] == [BackupFileStatus.DONE, BackupFileStatus.DONE]


def test_unreachable_sftp_server_fails_the_job_but_keeps_the_files_pending(db: Any, env: Env) -> None:
    env.add_recording(2)
    env.sftp.stop()
    job = env.job(db=db)
    assert backup.run_job(db, job.id) == BackupJobStatus.FAILED
    assert files(db)[0].status == BackupFileStatus.PENDING


def test_changed_host_key_is_refused_and_recorded_on_the_destination(db: Any, env: Env) -> None:
    env.add_recording(2)
    env.dest.host_key_sha256 = "SHA256:someoneelse"
    db.commit()
    job = env.job(db=db)
    assert backup.run_job(db, job.id) == BackupJobStatus.FAILED
    db.expire_all()
    assert "host key" in db.get(BackupJob, job.id).message
    assert db.get(SftpDestination, env.dest.id).last_test_ok is False and env.remote_files() == []


def test_a_truncated_download_is_rejected_not_uploaded(db: Any, env: Env) -> None:
    env.add_recording(2, size=100_000, reported_length=500_000)
    job = env.job(db=db)
    assert backup.run_job(db, job.id) == BackupJobStatus.FAILED
    row = files(db)[0]
    assert row.status == BackupFileStatus.FAILED and "Incomplete" in (row.error or "") and env.remote_files() == []


def test_rejected_nvr_credentials_stop_everything_after_one_attempt(db: Any, env: Env) -> None:
    env.add_recording(2)
    env.nvr_sim.password = "changed-on-the-nvr"
    job = env.job(db=db)
    assert backup.run_job(db, job.id) == BackupJobStatus.FAILED
    db.expire_all()
    assert db.get(Nvr, env.nvr.id).status == NvrStatus.AUTH_FAILED
    assert "Not retrying" in db.get(BackupJob, job.id).message
    assert len([p for p in env.nvr_sim.requests if "mediaFileFind" in p]) == 0  # never got past the handshake


def test_cancel_stops_before_any_download(db: Any, env: Env) -> None:
    env.add_recording(2)
    job = env.job(db=db)
    job.cancel_requested = True
    db.commit()
    assert backup.run_job(db, job.id) == BackupJobStatus.CANCELLED
    assert files(db)[0].status == BackupFileStatus.CANCELLED and env.remote_files() == []
    assert not [p for p in env.nvr_sim.requests if "RPC_Loadfile" in p]


def test_cancelling_a_queued_job_never_runs_it(db: Any, env: Env) -> None:
    job = env.job(db=db)
    backup.request_cancel(db, job)
    assert job.status == BackupJobStatus.CANCELLED and backup.run_job(db, job.id) is None


def test_a_crashed_worker_is_recovered_and_the_job_resumes(db: Any, env: Env) -> None:
    env.add_recording(2)
    job = env.job(db=db)
    row = BackupFile(
        job_id=job.id, camera_id=env.camera.id, destination_id=env.dest.id, stream=BackupStream.SUB, nvr_path="x",
        recording_start=utcnow() - timedelta(hours=3), recording_end=utcnow() - timedelta(hours=2),
        status=BackupFileStatus.DOWNLOADING, bytes_done=5,
    )  # fmt: skip
    db.add(row)
    job.status, job.heartbeat_at = BackupJobStatus.RUNNING, utcnow() - timedelta(minutes=30)  # the worker died
    db.commit()
    assert backup.run_job(db, job.id) is None  # a stale "running" job is not taken over by accident

    assert backup.recover_stale(db) == [job.id]
    db.expire_all()
    assert db.get(BackupJob, job.id).status == BackupJobStatus.QUEUED
    assert db.get(BackupFile, row.id).status == BackupFileStatus.PENDING
    assert backup.run_job(db, job.id) == BackupJobStatus.SUCCESS


def test_a_running_job_with_a_fresh_heartbeat_is_left_alone(db: Any, env: Env) -> None:
    job = env.job(db=db)
    job.status, job.heartbeat_at = BackupJobStatus.RUNNING, utcnow()
    db.commit()
    assert backup.recover_stale(db) == []


def test_only_one_job_runs_per_nvr_at_a_time(db: Any, env: Env) -> None:
    env.add_recording(2)
    busy = env.job(db=db)
    busy.status, busy.heartbeat_at = BackupJobStatus.RUNNING, utcnow()
    waiting = env.job(db=db)
    db.commit()
    assert backup.run_job(db, waiting.id) is None
    db.expire_all()
    assert db.get(BackupJob, waiting.id).status == BackupJobStatus.QUEUED


def test_refuses_to_start_when_the_staging_disk_is_nearly_full(db: Any, env: Env, monkeypatch: Any) -> None:
    env.add_recording(2)
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: type("U", (), {"free": 1000})())
    job = env.job(db=db)
    assert backup.run_job(db, job.id) == BackupJobStatus.FAILED
    db.expire_all()
    assert "free disk space" in db.get(BackupJob, job.id).message and env.remote_files() == []


def test_gaps_in_the_nvrs_recording_are_reported(db: Any, env: Env) -> None:
    env.add_recording(5)
    env.add_recording(2)  # nothing recorded for two hours in between
    job = env.job(db=db)
    backup.run_job(db, job.id)
    db.expire_all()
    assert "gap" in db.get(BackupJob, job.id).message


# ----------------------------------------------------------------------------------------------- scheduling
def test_scheduled_runs_advance_the_checkpoint_only_over_backed_up_recordings(db: Any, env: Env) -> None:
    r1 = env.add_recording(3)
    env.add_recording(2)
    inprog = env.add_in_progress()
    env.policy.checkpoint_at = utcnow() - timedelta(hours=6)
    db.commit()
    [job_id] = backup.schedule_due(db)
    assert backup.run_job(db, job_id) == BackupJobStatus.SUCCESS
    db.expire_all()
    policy = db.get(CameraBackupPolicy, env.policy.id)
    # stops where the still-being-written recording starts, so it is picked up by the next run
    assert policy.checkpoint_at == (inprog.start - timedelta(minutes=OFFSET)).replace(tzinfo=UTC)
    assert policy.next_run_at > utcnow() and policy.consecutive_failures == 0 and r1.data


def test_a_failed_recording_holds_the_checkpoint_back_and_failures_back_off(db: Any, env: Env) -> None:
    env.add_recording(3, size=900_000)
    env.add_recording(2)
    original = utcnow() - timedelta(hours=6)
    env.policy.checkpoint_at = original
    db.commit()
    env.sftp.fail_after_bytes = 100_000
    [job_id] = backup.schedule_due(db)
    assert backup.run_job(db, job_id) == BackupJobStatus.PARTIAL
    db.expire_all()
    policy = db.get(CameraBackupPolicy, env.policy.id)
    assert policy.checkpoint_at == original  # the first recording failed, so nothing after it is "passed"
    assert policy.consecutive_failures == 1
    assert policy.next_run_at - utcnow() < timedelta(minutes=5)  # retry soon, not in a full hour

    policy.next_run_at = utcnow() - timedelta(seconds=1)
    db.commit()
    [again] = backup.schedule_due(db)
    assert backup.run_job(db, again) == BackupJobStatus.SUCCESS
    db.expire_all()
    assert db.get(CameraBackupPolicy, env.policy.id).consecutive_failures == 0
    assert db.get(CameraBackupPolicy, env.policy.id).checkpoint_at > original


def test_schedule_skips_what_is_not_due_or_not_allowed(db: Any, env: Env) -> None:
    assert len(backup.schedule_due(db)) == 1  # due: never ran
    assert backup.schedule_due(db) == []  # a job for this camera is already queued
    db.execute(BackupJob.__table__.delete())
    env.policy.next_run_at = utcnow() + timedelta(minutes=30)
    db.commit()
    assert backup.schedule_due(db) == []  # not due yet
    env.policy.next_run_at = None
    env.nvr.status = NvrStatus.AUTH_FAILED
    db.commit()
    assert backup.schedule_due(db) == []  # never poke an NVR that rejected its credentials
    env.nvr.status = NvrStatus.CONNECTED
    env.policy.enabled = False
    db.commit()
    assert backup.schedule_due(db) == []


def test_scheduled_window_starts_a_little_before_the_checkpoint(db: Any, env: Env) -> None:
    cp = utcnow() - timedelta(hours=2)
    env.policy.checkpoint_at = cp
    db.commit()
    [job_id] = backup.schedule_due(db)
    job = db.get(BackupJob, job_id)
    assert job.window_start == cp - timedelta(minutes=10) and job.trigger == BackupTrigger.SCHEDULED
    assert isinstance(job_id, uuid.UUID)
