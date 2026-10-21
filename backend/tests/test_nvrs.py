import json
from typing import Any

from sqlalchemy import select

from app.core.security import decrypt_secret
from app.models import AuditLog, Nvr
from app.models.enums import Role
from tests.conftest import Api, make_user

PW = "S3cret-NVR-pass!"
SERIAL = "ABCD1234EFGH5678"


def lan(campus_id: str, **over: Any) -> dict[str, Any]:
    body = {
        "campus_id": campus_id,
        "name": "Main NVR",
        "connection_type": "lan",
        "host": "192.168.15.6",
        "username": "admin",
        "password": PW,
    }
    body.update(over)
    return body


def setup_campus(admin_api: Api, name: str = "C1") -> str:
    return str(admin_api.post("/api/v1/campuses", json={"name": name}).json()["id"])


def test_register_nvr_is_pending_and_never_leaks_secrets(admin_api: Api, db) -> None:
    cid = setup_campus(admin_api)
    r = admin_api.post("/api/v1/nvrs", json=lan(cid, connection_type="p2p", p2p_serial=SERIAL))
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending" and body["has_password"] is True
    assert body["p2p_serial_masked"] == "***5678"
    blob = json.dumps(body)
    assert PW not in blob and SERIAL not in blob and "password_encrypted" not in blob

    # list + detail responses are equally clean
    for resp in (admin_api.get("/api/v1/nvrs"), admin_api.get(f"/api/v1/nvrs/{body['id']}")):
        assert PW not in resp.text and SERIAL not in resp.text

    # at rest: encrypted, and recoverable only with the key
    db.expire_all()
    row = db.scalar(select(Nvr))
    assert row.password_encrypted != PW and PW not in row.password_encrypted
    assert decrypt_secret(row.password_encrypted) == PW
    assert decrypt_secret(row.p2p_serial_encrypted) == SERIAL

    # audit trail has the event but no secrets
    audit = db.scalars(select(AuditLog).where(AuditLog.action == "nvr.create")).all()
    assert len(audit) == 1 and PW not in json.dumps(audit[0].details) and SERIAL not in json.dumps(audit[0].details)


def test_connection_profile_validation(admin_api: Api) -> None:
    cid = setup_campus(admin_api)
    r = admin_api.post("/api/v1/nvrs", json=lan(cid, host=None))
    assert r.status_code == 422
    r = admin_api.post("/api/v1/nvrs", json=lan(cid, connection_type="tunnel", host=None))
    assert r.status_code == 422
    r = admin_api.post("/api/v1/nvrs", json=lan(cid, connection_type="p2p", host=None))  # no serial
    assert r.status_code == 422
    for bad in ("http://10.0.0.1", "10.0.0.1:80", "a b", "host/path", "-bad.example"):
        assert admin_api.post("/api/v1/nvrs", json=lan(cid, host=bad)).status_code == 422, bad
    for good in ("10.0.0.5", "nvr-1.campus.example.com", "nvr1"):
        assert admin_api.post("/api/v1/nvrs", json=lan(cid, host=good, name=f"n-{good}")).status_code == 201, good


def test_port_range_and_unknown_campus(admin_api: Api) -> None:
    cid = setup_campus(admin_api)
    assert admin_api.post("/api/v1/nvrs", json=lan(cid, http_port=0)).status_code == 422
    assert admin_api.post("/api/v1/nvrs", json=lan(cid, rtsp_port=70000)).status_code == 422
    r = admin_api.post("/api/v1/nvrs", json=lan("00000000-0000-0000-0000-000000000000"))
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "campus_id"


def test_name_unique_per_campus_only(admin_api: Api) -> None:
    c1, c2 = setup_campus(admin_api, "A"), setup_campus(admin_api, "B")
    assert admin_api.post("/api/v1/nvrs", json=lan(c1)).status_code == 201
    dup = admin_api.post("/api/v1/nvrs", json=lan(c1, name="main nvr"))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "nvr_name_taken"
    assert admin_api.post("/api/v1/nvrs", json=lan(c2)).status_code == 201


def test_update_keeps_password_when_omitted_and_resets_status_when_connection_changes(admin_api: Api, db) -> None:
    cid = setup_campus(admin_api)
    nid = admin_api.post("/api/v1/nvrs", json=lan(cid)).json()["id"]
    row = db.scalar(select(Nvr))
    row.status = "connected"
    db.commit()
    old_cipher = row.password_encrypted

    body = lan(cid, name="Renamed")
    body.pop("password")
    r = admin_api.put(f"/api/v1/nvrs/{nid}", json=body)
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    assert r.json()["status"] == "connected"  # nothing about the connection changed
    db.expire_all()
    assert db.scalar(select(Nvr)).password_encrypted == old_cipher

    r = admin_api.put(f"/api/v1/nvrs/{nid}", json={**body, "host": "192.168.15.99"})
    assert r.json()["status"] == "pending"  # a stale "connected" must not survive a new address

    r = admin_api.put(f"/api/v1/nvrs/{nid}", json={**body, "host": "192.168.15.99", "password": "new-pass"})
    assert r.status_code == 200
    db.expire_all()
    assert decrypt_secret(db.scalar(select(Nvr)).password_encrypted) == "new-pass"


def test_delete_is_soft_and_hides_nvr(admin_api: Api, db) -> None:
    cid = setup_campus(admin_api)
    nid = admin_api.post("/api/v1/nvrs", json=lan(cid)).json()["id"]
    assert admin_api.delete(f"/api/v1/nvrs/{nid}").status_code == 204
    assert admin_api.get(f"/api/v1/nvrs/{nid}").status_code == 404
    assert admin_api.get("/api/v1/nvrs").json()["total"] == 0
    db.expire_all()
    assert db.scalar(select(Nvr)).deleted_at is not None
    # name is free again
    assert admin_api.post("/api/v1/nvrs", json=lan(cid)).status_code == 201


def test_campus_scoped_visibility_and_write_protection(admin_api: Api, db) -> None:
    c1, c2 = setup_campus(admin_api, "Mine"), setup_campus(admin_api, "Other")
    n1 = admin_api.post("/api/v1/nvrs", json=lan(c1, name="N-mine")).json()["id"]
    n2 = admin_api.post("/api/v1/nvrs", json=lan(c2, name="N-other")).json()["id"]
    make_user(db, "op", Role.OPERATOR, [__import__("uuid").UUID(c1)])

    op = Api()
    op.login("op")
    names = [n["name"] for n in op.get("/api/v1/nvrs").json()["items"]]
    assert names == ["N-mine"]
    assert op.get(f"/api/v1/nvrs/{n1}").status_code == 200
    assert op.get(f"/api/v1/nvrs/{n2}").status_code == 404
    assert op.get(f"/api/v1/nvrs?campus_id={c2}").json()["total"] == 0
    assert op.post("/api/v1/nvrs", json=lan(c1, name="nope")).status_code == 403
    assert op.put(f"/api/v1/nvrs/{n1}", json=lan(c1)).status_code == 403
    assert op.delete(f"/api/v1/nvrs/{n1}").status_code == 403


def test_filters(admin_api: Api, db) -> None:
    cid = setup_campus(admin_api)
    admin_api.post("/api/v1/nvrs", json=lan(cid, name="Alpha"))
    admin_api.post("/api/v1/nvrs", json=lan(cid, name="Beta"))
    row = db.scalar(select(Nvr).where(Nvr.name == "Beta"))
    row.status = "offline"
    db.commit()
    assert admin_api.get("/api/v1/nvrs?status=offline").json()["total"] == 1
    assert admin_api.get("/api/v1/nvrs?q=alp").json()["items"][0]["name"] == "Alpha"
    assert admin_api.get("/api/v1/nvrs?status=bogus").status_code == 422
