from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.db.base import utcnow
from app.models.backup import SftpDestination
from app.models.camera import CameraBackupPolicy
from app.models.enums import Role
from tests.conftest import Api, make_campus, make_user
from tests.support.fake_sftp import PASSWORD, USER
from tests.test_backup_engine import Env, env  # noqa: F401 (pytest fixture)

B = "/api/v1/backups"


def dest_body(e: Env, **over: Any) -> dict[str, Any]:
    body = {
        "name": "Offsite", "host": "127.0.0.1", "port": e.sftp.port, "username": USER, "password": PASSWORD,
        "auth_type": "password", "base_path": "/offsite/", "host_key_sha256": e.sftp.fingerprint,
    }  # fmt: skip
    body.update(over)
    return body


# ------------------------------------------------------------------------------------------------ destinations
def test_destination_lifecycle_and_secrets_never_come_back(admin_api: Api, env: Env) -> None:
    r = admin_api.post(f"{B}/destinations", json=dest_body(env))
    assert r.status_code == 201
    d = r.json()
    assert d["has_secret"] is True and d["base_path"] == "/offsite" and d["last_test_ok"] is None
    assert PASSWORD not in r.text and "password" not in d and "private_key" not in d

    upd = admin_api.put(f"{B}/destinations/{d['id']}", json=dest_body(env, name="Offsite 2", password=None))
    assert upd.status_code == 200 and upd.json()["name"] == "Offsite 2" and upd.json()["has_secret"] is True
    assert PASSWORD not in admin_api.get(f"{B}/destinations").text
    assert admin_api.delete(f"{B}/destinations/{d['id']}").status_code == 204
    assert [x["name"] for x in admin_api.get(f"{B}/destinations").json()] == ["Archive"]  # the fixture's own


def test_host_key_probe_returns_the_fingerprint_to_confirm(admin_api: Api, env: Env) -> None:
    r = admin_api.post(f"{B}/destinations/host-key", json={"host": "127.0.0.1", "port": env.sftp.port})
    assert r.status_code == 200 and r.json()["sha256"] == env.sftp.fingerprint


def test_destination_validation(admin_api: Api, env: Env) -> None:
    assert admin_api.post(f"{B}/destinations", json=dest_body(env, password=None)).status_code == 422
    assert admin_api.post(f"{B}/destinations", json=dest_body(env, host_key_sha256="trust-me")).status_code == 422
    assert admin_api.post(f"{B}/destinations", json=dest_body(env, base_path="/a/../b")).status_code == 422
    assert admin_api.post(f"{B}/destinations", json=dest_body(env, host="http://evil")).status_code == 422
    assert admin_api.post(f"{B}/destinations", json=dest_body(env, name="Archive")).status_code == 409  # duplicate


def test_destination_test_connects_and_writes(admin_api: Api, env: Env) -> None:
    d = admin_api.post(f"{B}/destinations", json=dest_body(env)).json()
    r = admin_api.post(f"{B}/destinations/{d['id']}/test").json()
    assert r["last_test_ok"] is True and r["last_test_at"] and "wrote a test file" in r["last_test_message"]
    assert (env.remote / "offsite").is_dir()


def test_destination_test_reports_a_wrong_host_key_and_bad_login(admin_api: Api, env: Env) -> None:
    bad = admin_api.post(f"{B}/destinations", json=dest_body(env, host_key_sha256="SHA256:" + "A" * 43)).json()
    r = admin_api.post(f"{B}/destinations/{bad['id']}/test").json()
    assert r["last_test_ok"] is False and "does not match" in r["last_test_message"]
    wrong = admin_api.post(f"{B}/destinations", json=dest_body(env, name="Wrong login", password="nope")).json()
    r2 = admin_api.post(f"{B}/destinations/{wrong['id']}/test").json()
    assert r2["last_test_ok"] is False and "nope" not in r2["last_test_message"]


def test_destination_in_use_cannot_be_deleted_and_only_admins_manage_them(admin_api: Api, env: Env, db: Any) -> None:
    assert admin_api.delete(f"{B}/destinations/{env.dest.id}").status_code == 409  # the fixture policy uses it
    make_user(db, "op", Role.OPERATOR, [env.camera.campus_id])
    op = Api()
    op.login("op")
    assert op.get(f"{B}/destinations").status_code == 403
    assert op.post(f"{B}/destinations", json=dest_body(env)).status_code == 403


# ----------------------------------------------------------------------------------------------------- policies
def test_policy_list_shows_unconfigured_cameras_with_defaults(admin_api: Api, env: Env, db: Any) -> None:
    db.execute(CameraBackupPolicy.__table__.delete())
    db.commit()
    rows = admin_api.get(f"{B}/policies").json()["items"]
    assert len(rows) == 1 and rows[0]["configured"] is False and rows[0]["enabled"] is False
    assert rows[0]["backup_stream"] == "sub" and rows[0]["frequency_minutes"] == 60


def test_enabling_a_policy_needs_a_destination_and_starts_from_now_by_default(admin_api: Api, env: Env, db: Any) -> None:
    db.execute(CameraBackupPolicy.__table__.delete())
    db.commit()
    cam = str(env.camera.id)
    no_dest = admin_api.put(f"{B}/policies/{cam}", json={"enabled": True})
    assert no_dest.status_code == 422
    r = admin_api.put(f"{B}/policies/{cam}", json={"enabled": True, "destination_id": str(env.dest.id), "backup_stream": "main",
                                                   "frequency_minutes": 30, "backfill_hours": 0})  # fmt: skip
    assert r.status_code == 200
    p = r.json()
    assert p["enabled"] and p["backup_stream"] == "main" and p["frequency_minutes"] == 30
    assert p["destination_name"] == "Archive" and p["configured"] is True
    assert abs((utcnow() - __import__("datetime").datetime.fromisoformat(p["checkpoint_at"])).total_seconds()) < 30


def test_backfill_hours_set_how_far_back_the_first_run_looks(admin_api: Api, env: Env, db: Any) -> None:
    db.execute(CameraBackupPolicy.__table__.delete())
    db.commit()
    r = admin_api.put(f"{B}/policies/{env.camera.id}", json={"enabled": True, "destination_id": str(env.dest.id), "backfill_hours": 24})
    from datetime import datetime

    gap = utcnow() - datetime.fromisoformat(r.json()["checkpoint_at"])
    assert timedelta(hours=23, minutes=59) < gap < timedelta(hours=24, minutes=1)


def test_bulk_update_changes_only_the_given_fields(admin_api: Api, env: Env, db: Any) -> None:
    env.policy.backup_stream = __import__("app.models.enums", fromlist=["BackupStream"]).BackupStream.MAIN
    env.policy.frequency_minutes = 15
    db.commit()
    r = admin_api.post(f"{B}/policies/bulk", json={"camera_ids": [str(env.camera.id)], "recording_mode": "continuous"})
    assert r.status_code == 200
    p = r.json()[0]
    assert p["recording_mode"] == "continuous" and p["backup_stream"] == "main" and p["frequency_minutes"] == 15


def test_operators_manage_policies_only_in_their_campuses_and_viewers_not_at_all(
    admin_api: Api, env: Env, db: Any
) -> None:
    other = make_campus(db, "Other")
    make_user(db, "op", Role.OPERATOR, [env.camera.campus_id])
    make_user(db, "stranger", Role.OPERATOR, [other.id])
    make_user(db, "vw", Role.VIEWER, [env.camera.campus_id])
    body = {"enabled": True, "destination_id": str(env.dest.id)}
    op = Api()
    op.login("op")
    assert op.put(f"{B}/policies/{env.camera.id}", json=body).status_code == 200
    stranger = Api()
    stranger.login("stranger")
    assert stranger.put(f"{B}/policies/{env.camera.id}", json=body).status_code == 404
    assert stranger.get(f"{B}/policies").json()["total"] == 0
    viewer = Api()
    viewer.login("vw")
    assert viewer.put(f"{B}/policies/{env.camera.id}", json=body).status_code == 403
    assert viewer.get(f"{B}/policies").json()["total"] == 1  # read-only access


# --------------------------------------------------------------------------------------- recordings and manual
def window(hours: int = 8) -> dict[str, str]:
    now = utcnow()
    return {"start": (now - timedelta(hours=hours)).isoformat(), "end": now.isoformat()}


def test_recording_search_lists_files_marks_in_progress_and_backup_state(admin_api: Api, env: Env) -> None:
    env.add_recording(3)
    env.add_recording(2)
    env.add_in_progress()
    items = admin_api.get(f"{B}/cameras/{env.camera.id}/recordings", params=window()).json()["items"]
    assert [(i["in_progress"], i["backup_status"]) for i in items] == [(False, None), (False, None), (True, None)]
    assert items[0]["size_bytes"] == 300_000 and items[0]["stream"] == "sub"

    created = admin_api.post(f"{B}/jobs", json={"camera_id": str(env.camera.id), **window()})
    assert created.status_code == 201 and created.json()["status"] == "queued"  # the response is the job as queued
    job = admin_api.get(f"{B}/jobs/{created.json()['id']}").json()
    assert job["status"] == "success" and job["files_done"] == 2  # the eager worker ran it inline
    after = admin_api.get(f"{B}/cameras/{env.camera.id}/recordings", params=window()).json()["items"]
    assert [i["backup_status"] for i in after] == ["done", "done", None]
    assert after[0]["remote_path"].endswith(".dav")


def test_recording_search_validates_the_window_and_the_nvr_state(admin_api: Api, env: Env, db: Any) -> None:
    url = f"{B}/cameras/{env.camera.id}/recordings"
    assert admin_api.get(url, params={"start": "2026-10-22T00:00:00", "end": "2026-10-22T01:00:00"}).status_code == 422
    assert admin_api.get(url, params=window(hours=72)).status_code == 422
    env.nvr.utc_offset_minutes = None
    db.commit()
    assert admin_api.get(url, params=window()).status_code == 409


def test_recording_search_hides_other_campuses(admin_api: Api, env: Env, db: Any) -> None:
    make_user(db, "stranger", Role.OPERATOR, [make_campus(db, "Other").id])
    s = Api()
    s.login("stranger")
    assert s.get(f"{B}/cameras/{env.camera.id}/recordings", params=window()).status_code == 404


def test_manual_job_validation_and_one_active_job_per_camera(admin_api: Api, env: Env, db: Any) -> None:
    cam = str(env.camera.id)
    now = utcnow()
    too_long = {"start": (now - timedelta(days=9)).isoformat(), "end": now.isoformat()}
    assert admin_api.post(f"{B}/jobs", json={"camera_id": cam, **too_long}).status_code == 422
    future = {"start": now.isoformat(), "end": (now + timedelta(hours=3)).isoformat()}
    assert admin_api.post(f"{B}/jobs", json={"camera_id": cam, **future}).status_code == 422
    backwards = {"start": now.isoformat(), "end": (now - timedelta(hours=1)).isoformat()}
    assert admin_api.post(f"{B}/jobs", json={"camera_id": cam, **backwards}).status_code == 422

    db.execute(CameraBackupPolicy.__table__.delete())
    db.commit()
    assert admin_api.post(f"{B}/jobs", json={"camera_id": cam, **window()}).status_code == 422  # no destination known

    from app.models.enums import BackupJobStatus, BackupStream, BackupTrigger
    from app.services import backup

    stuck = backup.create_job(db, env.camera, env.nvr, env.dest, BackupStream.SUB, BackupTrigger.MANUAL, now - timedelta(hours=2), now)
    stuck.status = BackupJobStatus.RUNNING
    db.commit()
    again = admin_api.post(f"{B}/jobs", json={"camera_id": cam, "destination_id": str(env.dest.id), **window()})
    assert again.status_code == 409 and again.json()["error"]["code"] == "backup_already_active"


def test_job_list_detail_and_scoping(admin_api: Api, env: Env, db: Any) -> None:
    env.add_recording(2)
    created = admin_api.post(f"{B}/jobs", json={"camera_id": str(env.camera.id), **window()}).json()
    job = admin_api.get(f"{B}/jobs/{created['id']}").json()
    assert job["camera_name"] == "Lobby Cam" and job["destination_name"] == "Archive" and job["requested_by"] == "admin"
    listing = admin_api.get(f"{B}/jobs", params={"status": "success"}).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == job["id"]
    assert admin_api.get(f"{B}/jobs", params={"active": "true"}).json()["total"] == 0
    detail = admin_api.get(f"{B}/jobs/{job['id']}").json()
    assert len(detail["files"]) == 1 and detail["files"][0]["status"] == "done" and detail["files"][0]["sha256"]

    make_user(db, "stranger", Role.OPERATOR, [make_campus(db, "Other").id])
    s = Api()
    s.login("stranger")
    assert s.get(f"{B}/jobs/{job['id']}").status_code == 404 and s.get(f"{B}/jobs").json()["total"] == 0


def test_cancel_and_retry(admin_api: Api, env: Env, db: Any) -> None:
    from app.models.enums import BackupJobStatus, BackupStream, BackupTrigger
    from app.services import backup

    now = utcnow()
    queued = backup.create_job(db, env.camera, env.nvr, env.dest, BackupStream.SUB, BackupTrigger.MANUAL, now - timedelta(hours=2), now)
    db.commit()
    c = admin_api.post(f"{B}/jobs/{queued.id}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert admin_api.post(f"{B}/jobs/{queued.id}/cancel").status_code == 409  # already finished

    env.add_recording(2, size=500_000)
    env.sftp.stop()  # the destination is down, so the run fails
    created = admin_api.post(f"{B}/jobs", json={"camera_id": str(env.camera.id), **window()}).json()
    failed = admin_api.get(f"{B}/jobs/{created['id']}").json()
    assert failed["status"] == "failed"
    env.sftp = type(env.sftp)(env.remote).start()  # a new server (new port and key)
    d = db.get(SftpDestination, env.dest.id)
    d.port, d.host_key_sha256 = env.sftp.port, env.sftp.fingerprint
    db.commit()
    retry = admin_api.post(f"{B}/jobs/{failed['id']}/retry")
    assert retry.status_code == 201
    assert admin_api.get(f"{B}/jobs/{retry.json()['id']}").json()["status"] == BackupJobStatus.SUCCESS.value
    env.sftp.stop()


def test_status_reports_worker_counts_and_failing_destinations(admin_api: Api, env: Env, db: Any) -> None:
    st = admin_api.get(f"{B}/status").json()
    assert st["worker_online"] is True and st["active_jobs"] == 0 and st["destinations_failing"] == 0
    assert st["staging_free_gb"] is None or st["staging_free_gb"] > 0
    env.dest.last_test_ok = False
    db.commit()
    assert admin_api.get(f"{B}/status").json()["destinations_failing"] == 1


def test_only_signed_in_users_can_use_the_backup_api(api: Api) -> None:
    assert api.get(f"{B}/jobs").status_code == 401 and api.get(f"{B}/destinations").status_code == 401
    assert select(SftpDestination) is not None
