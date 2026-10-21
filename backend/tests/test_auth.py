from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AuditLog, RefreshSession, User
from app.models.enums import Role
from tests.conftest import PASSWORD, Api, make_user


def test_login_sets_httponly_cookies_and_returns_user(api: Api, admin: User) -> None:
    r = api.login("admin")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin" and body["role"] == "admin"
    assert "password" not in str(body).lower()
    set_cookie = " ".join(r.headers.get_list("set-cookie")).lower()
    assert "access_token=" in set_cookie and "refresh_token=" in set_cookie and "csrf_token=" in set_cookie
    # access + refresh must be HttpOnly; csrf must not be (the SPA reads it)
    cookies = {c.split("=")[0].strip(): c.lower() for c in r.headers.get_list("set-cookie")}
    assert "httponly" in cookies["access_token"] and "httponly" in cookies["refresh_token"]
    assert "httponly" not in cookies["csrf_token"]


def test_username_is_case_insensitive(api: Api, admin: User) -> None:
    assert api.login("ADMIN").status_code == 200


def test_wrong_password_and_unknown_user_look_identical(api: Api, admin: User) -> None:
    a = api.login("admin", "wrong-password-here")
    b = api.login("nobody", "wrong-password-here")
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["message"] == b.json()["error"]["message"]
    assert a.json()["error"]["code"] == "invalid_credentials"


def test_account_locks_after_repeated_failures(api: Api, admin: User) -> None:
    for _ in range(5):
        assert api.login("admin", "bad-password-123").status_code == 401
    # even the right password is refused while locked
    r = api.login("admin")
    assert r.status_code == 429 and r.json()["error"]["code"] == "account_locked"


def test_lock_expires(api: Api, admin: User, db) -> None:
    for _ in range(5):
        api.login("admin", "bad-password-123")
    db.expire_all()
    user = db.scalar(select(User).where(User.username == "admin"))
    user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert api.login("admin").status_code == 200


def test_ip_rate_limit(api: Api) -> None:
    codes = [api.login(f"user{i}", "whatever-password").status_code for i in range(25)]
    assert codes[:20] == [401] * 20
    assert 429 in codes[20:]


def test_disabled_account_cannot_login(api: Api, db) -> None:
    make_user(db, "gone", Role.VIEWER, active=False)
    r = api.login("gone")
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_disabled"


def test_me_requires_authentication(api: Api) -> None:
    r = api.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"


def test_cookie_auth_requires_csrf_header_on_writes(admin_api: Api) -> None:
    r = admin_api.client.post("/api/v1/campuses", json={"name": "A"})  # no X-CSRF-Token
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    wrong = admin_api.client.post("/api/v1/campuses", json={"name": "A"}, headers={"X-CSRF-Token": "nope"})
    assert wrong.status_code == 403
    assert admin_api.post("/api/v1/campuses", json={"name": "A"}).status_code == 201


def test_bearer_token_works_without_csrf(admin: User) -> None:
    from app.core.security import create_access_token

    token = create_access_token(str(admin.id), "admin")
    fresh = TestClient(app)  # no cookies
    r = fresh.post("/api/v1/campuses", json={"name": "ViaBearer"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201


def test_garbage_and_wrong_type_tokens_rejected() -> None:
    fresh = TestClient(app)
    assert fresh.get("/api/v1/auth/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_logout_revokes_refresh_session(admin_api: Api, db) -> None:
    assert admin_api.post("/api/v1/auth/logout").status_code == 204
    assert admin_api.get("/api/v1/auth/me").status_code == 401
    assert db.scalar(select(RefreshSession).where(RefreshSession.revoked_at.is_(None))) is None


def test_refresh_rotates_token_and_reuse_kills_all_sessions(api: Api, admin: User, db) -> None:
    api.login("admin")
    old_refresh = api.client.cookies.get("refresh_token", path="/api/v1/auth")
    assert api.post("/api/v1/auth/refresh").status_code == 200
    new_refresh = api.client.cookies.get("refresh_token", path="/api/v1/auth")
    assert new_refresh != old_refresh

    # an attacker replays the old, already-rotated token
    api.client.cookies.set("refresh_token", old_refresh, path="/api/v1/auth")
    assert api.post("/api/v1/auth/refresh").status_code == 401
    db.expire_all()
    assert db.scalar(select(RefreshSession).where(RefreshSession.revoked_at.is_(None))) is None
    reuse = db.scalar(select(AuditLog).where(AuditLog.action == "auth.refresh_reuse"))
    assert reuse is not None and reuse.success is False


def test_disabling_user_cuts_off_existing_access_token(admin_api: Api, db) -> None:
    viewer = make_user(db, "viewer1", Role.VIEWER)
    other = Api()
    assert other.login("viewer1").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 200
    assert admin_api.patch(f"/api/v1/users/{viewer.id}", json={"is_active": False}).status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401


def test_login_events_are_audited_without_secrets(api: Api, admin: User, db) -> None:
    api.login("admin", "bad-password-123")
    api.login("admin")
    rows = list(db.scalars(select(AuditLog).where(AuditLog.action == "auth.login").order_by(AuditLog.created_at)))
    assert [r.success for r in rows] == [False, True]
    dumped = " ".join(str(r.details) for r in rows)
    assert PASSWORD not in dumped and "bad-password-123" not in dumped
