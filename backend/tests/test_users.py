from sqlalchemy import select

from app.models import AuditLog
from app.models.enums import Role
from tests.conftest import PASSWORD, Api, make_campus, make_user


def test_admin_creates_user_who_can_log_in(admin_api: Api, db) -> None:
    c = make_campus(db, "C1")
    r = admin_api.post(
        "/api/v1/users",
        json={
            "username": "Sam",
            "password": "a-long-enough-password",
            "role": "operator",
            "campus_ids": [str(c.id)],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "sam" and body["campus_ids"] == [str(c.id)]
    assert "password" not in str(body).lower() and "hash" not in str(body).lower()
    assert Api().login("sam", "a-long-enough-password").status_code == 200


def test_weak_password_and_bad_username_rejected(admin_api: Api) -> None:
    r = admin_api.post("/api/v1/users", json={"username": "sam", "password": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "weak_password"
    r = admin_api.post("/api/v1/users", json={"username": "sam", "password": "sam"})
    assert r.status_code == 422
    r = admin_api.post("/api/v1/users", json={"username": "bad name!", "password": "a-long-enough-password"})
    assert r.status_code == 422


def test_duplicate_username_conflict(admin_api: Api) -> None:
    body = {"username": "dup", "password": "a-long-enough-password"}
    assert admin_api.post("/api/v1/users", json=body).status_code == 201
    assert admin_api.post("/api/v1/users", json={**body, "username": "DUP"}).status_code == 409


def test_unknown_campus_rejected(admin_api: Api) -> None:
    r = admin_api.post(
        "/api/v1/users",
        json={
            "username": "sam",
            "password": "a-long-enough-password",
            "campus_ids": ["00000000-0000-0000-0000-000000000000"],
        },
    )
    assert r.status_code == 422


def test_non_admins_cannot_manage_users(db) -> None:
    make_user(db, "op", Role.OPERATOR)
    a = Api()
    a.login("op")
    assert a.get("/api/v1/users").status_code == 403
    assert a.post("/api/v1/users", json={"username": "x" * 3, "password": "a-long-enough-password"}).status_code == 403
    assert a.get("/api/v1/audit-logs").status_code == 403


def test_cannot_demote_or_disable_last_admin_or_self(admin_api: Api, admin, db) -> None:
    r = admin_api.patch(f"/api/v1/users/{admin.id}", json={"role": "viewer"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_admin"
    assert admin_api.patch(f"/api/v1/users/{admin.id}", json={"is_active": False}).status_code == 409

    second = make_user(db, "admin2", Role.ADMIN)
    # even with another admin, you cannot lock yourself out
    assert admin_api.patch(f"/api/v1/users/{admin.id}", json={"role": "viewer"}).status_code == 409
    # but demoting the other admin is fine while one remains
    assert admin_api.patch(f"/api/v1/users/{second.id}", json={"role": "viewer"}).status_code == 200


def test_set_campuses_replaces_assignments(admin_api: Api, db) -> None:
    c1, c2 = make_campus(db, "A"), make_campus(db, "B")
    u = make_user(db, "vw", Role.VIEWER, [c1.id])
    r = admin_api.put(f"/api/v1/users/{u.id}/campuses", json={"campus_ids": [str(c2.id)]})
    assert r.status_code == 200 and r.json()["campus_ids"] == [str(c2.id)]
    viewer = Api()
    viewer.login("vw")
    assert [c["name"] for c in viewer.get("/api/v1/campuses").json()["items"]] == ["B"]


def test_password_change_revokes_sessions_and_clears_lock(admin_api: Api, db) -> None:
    u = make_user(db, "vw", Role.VIEWER)
    viewer = Api()
    viewer.login("vw")
    refresh_before = viewer.client.cookies.get("refresh_token", path="/api/v1/auth")
    assert admin_api.patch(f"/api/v1/users/{u.id}", json={"password": "brand-new-password-1"}).status_code == 200
    viewer.client.cookies.set("refresh_token", refresh_before, path="/api/v1/auth")
    assert viewer.post("/api/v1/auth/refresh").status_code == 401
    assert Api().login("vw", PASSWORD).status_code == 401
    assert Api().login("vw", "brand-new-password-1").status_code == 200


def test_audit_log_lists_admin_actions_without_passwords(admin_api: Api) -> None:
    admin_api.post("/api/v1/users", json={"username": "sam", "password": "a-long-enough-password"})
    page = admin_api.get("/api/v1/audit-logs?action=user.").json()
    assert page["total"] == 1 and page["items"][0]["actor_username"] == "admin"
    assert "a-long-enough-password" not in str(page)


def test_dashboard_summary_respects_campus_scope(admin_api: Api, db) -> None:
    c1 = make_campus(db, "A")
    make_campus(db, "B")
    make_user(db, "vw", Role.VIEWER, [c1.id])
    assert admin_api.get("/api/v1/dashboard/summary").json()["campuses"] == 2
    v = Api()
    v.login("vw")
    s = v.get("/api/v1/dashboard/summary").json()
    assert (
        s["campuses"] == 1 and s["nvrs_total"] == 0 and set(s["nvrs_by_status"]) >= {"pending", "connected", "offline"}
    )


def test_audit_rows_exist_for_denied_login_only_once(api: Api, db) -> None:
    api.login("ghost", "whatever-password")
    rows = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login")).all()
    assert len(rows) == 1 and rows[0].actor_username == "ghost" and rows[0].success is False
