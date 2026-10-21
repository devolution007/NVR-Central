from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update

from app.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    CurrentUser,
    DbSession,
    check_csrf,
)
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    hash_token,
    needs_rehash,
    new_csrf_token,
    new_opaque_token,
    verify_password,
)
from app.models.user import RefreshSession, User
from app.schemas.user import LoginIn, UserDetailOut
from app.services import audit
from app.services.ratelimit import login_limiter
from app.services.users import user_detail

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_PATH = "/api/v1/auth"


def _set_session_cookies(response: Response, user: User, refresh_token: str) -> None:
    s = get_settings()
    common = {"secure": s.cookie_secure, "samesite": "lax"}
    response.set_cookie(
        ACCESS_COOKIE,
        create_access_token(str(user.id), user.role.value),
        max_age=s.access_token_minutes * 60,
        httponly=True,
        path="/",
        **common,  # type: ignore[arg-type]
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=s.refresh_token_days * 86400,
        httponly=True,
        path=REFRESH_PATH,
        **common,  # type: ignore[arg-type]
    )
    # Readable by the SPA on purpose: it echoes this value in X-CSRF-Token (double-submit).
    response.set_cookie(
        CSRF_COOKIE,
        new_csrf_token(),
        max_age=s.refresh_token_days * 86400,
        httponly=False,
        path="/",
        **common,  # type: ignore[arg-type]
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)
    response.delete_cookie(CSRF_COOKIE, path="/")


def _new_refresh_session(db: DbSession, request: Request, user: User) -> str:
    token = new_opaque_token()
    db.add(
        RefreshSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days),
            user_agent=(request.headers.get("user-agent") or "")[:256],
            ip_address=audit.client_ip(request),
        )
    )
    return token


_user_out = user_detail


@router.post("/login", response_model=UserDetailOut)
def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> UserDetailOut:
    s = get_settings()
    ip = audit.client_ip(request) or "unknown"
    if not login_limiter.allow(f"ip:{ip}", s.login_ip_limit_per_minute):
        raise ApiError(429, "rate_limited", "Too many login attempts. Try again in a minute.")

    username = payload.username.strip().lower()
    user = db.scalar(select(User).where(User.username == username, User.deleted_at.is_(None)))
    now = datetime.now(UTC)

    if user is None:
        verify_password(payload.password, DUMMY_HASH)
        audit.record(
            db, request, "auth.login", actor_username=username, success=False, details={"reason": "unknown_user"}
        )
        db.commit()
        raise ApiError(401, "invalid_credentials", "Invalid username or password")

    if user.locked_until and user.locked_until > now:
        audit.record(db, request, "auth.login", actor=user, success=False, details={"reason": "locked"})
        db.commit()
        raise ApiError(
            429, "account_locked", "Account temporarily locked after repeated failed logins. Try again later."
        )

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= s.login_max_failures:
            user.locked_until = now + timedelta(minutes=s.login_lock_minutes)
            user.failed_login_count = 0
        audit.record(db, request, "auth.login", actor=user, success=False, details={"reason": "bad_password"})
        db.commit()
        raise ApiError(401, "invalid_credentials", "Invalid username or password")

    if not user.is_active:
        audit.record(db, request, "auth.login", actor=user, success=False, details={"reason": "disabled"})
        db.commit()
        raise ApiError(403, "account_disabled", "This account is disabled")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    refresh = _new_refresh_session(db, request, user)
    audit.record(db, request, "auth.login", actor=user)
    db.commit()

    _set_session_cookies(response, user, refresh)
    return _user_out(db, user)


@router.post("/refresh", response_model=UserDetailOut)
def refresh(request: Request, response: Response, db: DbSession) -> UserDetailOut:
    check_csrf(request)
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise ApiError(401, "not_authenticated", "Authentication required")

    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == hash_token(token)))
    now = datetime.now(UTC)
    if session is None:
        _clear_session_cookies(response)
        raise ApiError(401, "invalid_token", "Session expired or invalid")

    if session.revoked_at is not None:
        # A rotated-out token was presented again: assume theft and end every session of this user.
        db.execute(
            update(RefreshSession)
            .where(RefreshSession.user_id == session.user_id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        audit.record(db, request, "auth.refresh_reuse", entity_type="user", entity_id=session.user_id, success=False)
        db.commit()
        raise ApiError(401, "invalid_token", "Session expired or invalid")

    user = db.get(User, session.user_id)
    if session.expires_at <= now or user is None or user.deleted_at is not None or not user.is_active:
        session.revoked_at = now
        db.commit()
        raise ApiError(401, "invalid_token", "Session expired or invalid")

    session.revoked_at = now
    new_token = _new_refresh_session(db, request, user)
    db.commit()
    _set_session_cookies(response, user, new_token)
    return _user_out(db, user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DbSession) -> Response:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        check_csrf(request)
        session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == hash_token(token)))
        if session is not None and session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            user = db.get(User, session.user_id)
            audit.record(db, request, "auth.logout", actor=user)
            db.commit()
    _clear_session_cookies(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserDetailOut)
def me(user: CurrentUser, db: DbSession) -> UserDetailOut:
    return _user_out(db, user)
