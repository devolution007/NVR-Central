"""NVR actions and camera inventory through the HTTP API (Celery runs eagerly; the adapter is SIMULATED)."""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.integrations.adapter_factory import override_adapter
from app.integrations.base import NvrAuthError
from app.models import AuditLog, Nvr
from app.models.enums import NvrStatus, Role
from app.services import nvr_ops
from app.workers.dispatch import QueueUnavailableError
from tests.conftest import Api, make_user
from tests.support.scripted import ScriptedAdapter, builder_for, channel

PW = "S3cret-NVR-pass!"


@pytest.fixture
def adapter():
    a = ScriptedAdapter()
    with override_adapter(builder_for(a)):
        yield a


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(nvr_ops, "RETRY_DELAY_SECONDS", 0)


def body(campus_id: str, **over: Any) -> dict[str, Any]:
    b = {"campus_id": campus_id, "name": "Main NVR", "connection_type": "lan", "host": "10.0.0.5",
         "username": "admin", "password": PW}  # fmt: skip
    b.update(over)
    return b


def campus(api: Api, name: str = "C1") -> str:
    return str(api.post("/api/v1/campuses", json={"name": name}).json()["id"])


def test_registering_an_nvr_connects_and_discovers_in_the_background(admin_api: Api, adapter) -> None:
    cid = campus(admin_api)
    r = admin_api.post("/api/v1/nvrs", json=body(cid))
    assert r.status_code == 201
    n = r.json()
    assert n["status"] == "connected" and n["model"] == "DHI-SIM-NVR" and n["camera_count"] == 2
    assert n["utc_offset_minutes"] == 300 and n["last_success_at"] is not None
    cams = admin_api.get(f"/api/v1/nvrs/{n['id']}/cameras").json()
    assert [(c["channel_number"], c["name"], c["status"]) for c in cams] == [
        (1, "Camera 1", "online"),
        (2, "Camera 2", "online"),
    ]
    assert cams[0]["main_stream"]["width"] == 1920 and cams[0]["sub_stream"]["height"] == 576
    assert PW not in json.dumps(n) and PW not in json.dumps(cams)


def test_registration_can_skip_the_automatic_check(admin_api: Api, adapter) -> None:
    n = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api), auto_connect=False)).json()
    assert n["status"] == "pending" and adapter.connect_calls == 0


def test_failed_first_connection_still_saves_the_nvr_with_the_reason(admin_api: Api, adapter) -> None:
    adapter.raise_on_connect = NvrAuthError("The NVR rejected the username or password (or the account is locked).")
    r = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api)))
    assert r.status_code == 201
    assert r.json()["status"] == "auth_failed" and "rejected" in r.json()["status_message"]


def test_queue_outage_does_not_lose_the_registration(admin_api: Api, adapter, monkeypatch) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise QueueUnavailableError("down")

    monkeypatch.setattr("app.api.v1.nvrs.enqueue", boom)
    r = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api)))
    assert r.status_code == 201
    assert r.json()["status"] == "pending" and "queue is unavailable" in r.json()["status_message"]


@pytest.mark.parametrize("action", ["test", "discover", "reconnect"])
def test_actions_are_queued_audited_and_resolve(admin_api: Api, adapter, db, action: str) -> None:
    nid = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api), auto_connect=False)).json()["id"]
    r = admin_api.post(f"/api/v1/nvrs/{nid}/{action}")
    assert r.status_code == 202 and r.json() == {"nvr_id": nid, "operation": action, "status": "queued"}
    assert admin_api.get(f"/api/v1/nvrs/{nid}").json()["status"] == "connected"
    assert db.scalar(select(AuditLog).where(AuditLog.action == f"nvr.{action}")) is not None


def test_test_action_leaves_cameras_and_discover_adds_them(admin_api: Api, adapter) -> None:
    nid = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api), auto_connect=False)).json()["id"]
    admin_api.post(f"/api/v1/nvrs/{nid}/test")
    assert admin_api.get(f"/api/v1/nvrs/{nid}/cameras").json() == []
    admin_api.post(f"/api/v1/nvrs/{nid}/discover")
    assert len(admin_api.get(f"/api/v1/nvrs/{nid}/cameras").json()) == 2


def test_rejected_credentials_block_automatic_retry_until_forced_or_edited(admin_api: Api, adapter) -> None:
    adapter.raise_on_connect = NvrAuthError("rejected")
    cid = campus(admin_api)
    nid = admin_api.post("/api/v1/nvrs", json=body(cid)).json()["id"]
    assert admin_api.get(f"/api/v1/nvrs/{nid}").json()["status"] == "auth_failed"
    attempts = adapter.connect_calls

    r = admin_api.post(f"/api/v1/nvrs/{nid}/test")
    assert r.status_code == 409 and r.json()["error"]["code"] == "credentials_rejected"
    assert adapter.connect_calls == attempts  # the NVR was not contacted again

    assert admin_api.post(f"/api/v1/nvrs/{nid}/test?force=true").status_code == 202  # deliberate retry
    assert adapter.connect_calls == attempts + 1

    # editing the credentials resets the status so checks resume
    adapter.raise_on_connect = None
    upd = body(cid, password="new-password")
    upd.pop("auto_connect", None)
    assert admin_api.put(f"/api/v1/nvrs/{nid}", json=upd).json()["status"] == "pending"
    assert admin_api.post(f"/api/v1/nvrs/{nid}/test").status_code == 202
    assert admin_api.get(f"/api/v1/nvrs/{nid}").json()["status"] == "connected"


def test_disabled_nvr_cannot_be_tested(admin_api: Api, adapter) -> None:
    nid = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api), enabled=False)).json()["id"]
    r = admin_api.post(f"/api/v1/nvrs/{nid}/test")
    assert r.status_code == 409 and r.json()["error"]["code"] == "nvr_disabled"


def test_second_action_while_one_is_running_is_rejected(admin_api: Api, adapter, db) -> None:
    nid = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api), auto_connect=False)).json()["id"]
    assert nvr_ops.claim(db, uuid.UUID(nid)) is not None  # a worker is busy with it
    r = admin_api.post(f"/api/v1/nvrs/{nid}/test")
    assert r.status_code == 409 and r.json()["error"]["code"] == "operation_in_progress"


def test_queue_outage_on_an_action_restores_the_previous_status(admin_api: Api, adapter, monkeypatch) -> None:
    nid = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api))).json()["id"]
    assert admin_api.get(f"/api/v1/nvrs/{nid}").json()["status"] == "connected"

    def boom(*_: Any, **__: Any) -> None:
        raise QueueUnavailableError("down")

    monkeypatch.setattr("app.api.v1.nvrs.enqueue", boom)
    r = admin_api.post(f"/api/v1/nvrs/{nid}/test")
    assert r.status_code == 503 and r.json()["error"]["code"] == "worker_unavailable"
    now = admin_api.get(f"/api/v1/nvrs/{nid}").json()
    assert now["status"] == "connected" and "queue is unavailable" in now["status_message"]  # not stuck on "connecting"


def test_only_admins_can_trigger_actions(admin_api: Api, adapter, db) -> None:
    cid = campus(admin_api)
    nid = admin_api.post("/api/v1/nvrs", json=body(cid)).json()["id"]
    make_user(db, "op", Role.OPERATOR, [uuid.UUID(cid)])
    op = Api()
    op.login("op")
    for action in ("test", "discover", "reconnect"):
        assert op.post(f"/api/v1/nvrs/{nid}/{action}").status_code == 403


def test_capabilities_endpoint_lists_every_capability_with_unknown_for_untested(admin_api: Api, adapter) -> None:
    nid = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api), auto_connect=False)).json()["id"]
    before = {c["name"]: c["status"] for c in admin_api.get(f"/api/v1/nvrs/{nid}/capabilities").json()}
    assert set(before) == {"p2p_auth", "device_info", "channel_discovery", "live_video", "recording_search",
                           "recording_download", "snapshot"}  # fmt: skip
    assert set(before.values()) == {"unknown"}
    admin_api.post(f"/api/v1/nvrs/{nid}/discover")
    after = {c["name"]: c["status"] for c in admin_api.get(f"/api/v1/nvrs/{nid}/capabilities").json()}
    assert after["live_video"] == "supported_tested" and after["recording_download"] == "unknown"


def test_camera_endpoints_filter_and_respect_campus_scope(admin_api: Api, adapter, db) -> None:
    c1, c2 = campus(admin_api, "Mine"), campus(admin_api, "Other")
    admin_api.post("/api/v1/nvrs", json=body(c1, name="N1"))
    adapter.channels = [channel(1, "Door")]
    admin_api.post("/api/v1/nvrs", json=body(c2, name="N2"))
    assert admin_api.get("/api/v1/cameras").json()["total"] == 3
    assert admin_api.get(f"/api/v1/cameras?campus_id={c2}").json()["total"] == 1
    assert admin_api.get("/api/v1/cameras?q=door").json()["items"][0]["campus_name"] == "Other"
    assert admin_api.get("/api/v1/cameras?status=offline").json()["total"] == 0
    assert admin_api.get("/api/v1/cameras?status=bogus").status_code == 422

    make_user(db, "vw", Role.VIEWER, [uuid.UUID(c1)])
    vw = Api()
    vw.login("vw")
    assert {c["campus_name"] for c in vw.get("/api/v1/cameras").json()["items"]} == {"Mine"}
    hidden = admin_api.get(f"/api/v1/cameras?campus_id={c2}").json()["items"][0]["id"]
    assert vw.get(f"/api/v1/cameras/{hidden}").status_code == 404
    assert vw.get(f"/api/v1/cameras/{hidden}/status").status_code == 404
    mine = vw.get("/api/v1/cameras").json()["items"][0]["id"]
    st = vw.get(f"/api/v1/cameras/{mine}/status").json()
    assert st["status"] == "online" and st["checked_at"] is not None


def test_deleted_nvr_hides_its_cameras_but_keeps_them_in_the_database(admin_api: Api, adapter, db) -> None:
    nid = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api))).json()["id"]
    admin_api.delete(f"/api/v1/nvrs/{nid}")
    assert admin_api.get("/api/v1/cameras").json()["total"] == 0
    assert admin_api.get(f"/api/v1/nvrs/{nid}/cameras").status_code == 404
    from app.models import Camera

    assert len(list(db.scalars(select(Camera)))) == 2  # history is kept


def test_loopback_target_is_refused_when_the_flag_is_off(admin_api: Api, monkeypatch, db) -> None:
    from app.integrations import hostguard

    monkeypatch.setattr(hostguard, "get_settings", lambda: type("S", (), {"allow_loopback_nvr_hosts": False})())
    r = admin_api.post("/api/v1/nvrs", json=body(campus(admin_api), host="127.0.0.1"))
    assert r.status_code == 201
    row = db.scalar(select(Nvr))
    assert row.status == NvrStatus.UNSUPPORTED and "not allowed" in (row.status_message or "")
