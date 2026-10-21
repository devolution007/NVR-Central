import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, forbidden
from app.core.security import decode_access_token, tokens_match
from app.db.session import get_db
from app.models.enums import Role
from app.models.user import User, UserCampusPermission

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DbSession = Annotated[Session, Depends(get_db)]


def check_csrf(request: Request) -> None:
    """Double-submit check for cookie-authenticated, state-changing requests."""
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not tokens_match(cookie, header):
        raise ApiError(403, "csrf_failed", "CSRF token missing or invalid")


def get_current_user(request: Request, db: DbSession) -> User:
    token = request.cookies.get(ACCESS_COOKIE)
    from_cookie = token is not None
    if token is None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        raise ApiError(401, "not_authenticated", "Authentication required")

    payload = decode_access_token(token)
    if payload is None:
        raise ApiError(401, "invalid_token", "Session expired or invalid")

    if from_cookie and request.method not in SAFE_METHODS:
        check_csrf(request)

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise ApiError(401, "invalid_token", "Session expired or invalid") from exc

    # Always re-read the user: disabling an account or changing its role takes effect immediately.
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None or not user.is_active:
        raise ApiError(401, "invalid_token", "Session expired or invalid")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[[User], User]:
    def dep(user: CurrentUser) -> User:
        if user.role not in roles:
            raise forbidden()
        return user

    return dep


AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
OperatorUser = Annotated[User, Depends(require_roles(Role.ADMIN, Role.OPERATOR))]


def allowed_campus_ids(db: Session, user: User) -> set[uuid.UUID] | None:
    """None means unrestricted (admin). Otherwise the set of campuses the user was granted."""
    if user.role == Role.ADMIN:
        return None
    rows = db.scalars(select(UserCampusPermission.campus_id).where(UserCampusPermission.user_id == user.id))
    return set(rows)


def assert_campus_access(db: Session, user: User, campus_id: uuid.UUID) -> None:
    allowed = allowed_campus_ids(db, user)
    if allowed is not None and campus_id not in allowed:
        # 404 rather than 403 so campus existence is not revealed to users without access.
        raise ApiError(404, "not_found", "Campus not found")
