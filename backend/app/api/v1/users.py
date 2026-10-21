import uuid

from fastapi import APIRouter, Query, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, DbSession
from app.core.errors import ApiError, conflict, not_found
from app.core.security import PasswordPolicyError, hash_password, validate_password_policy
from app.db.base import utcnow
from app.models.campus import Campus
from app.models.enums import Role
from app.models.user import RefreshSession, User, UserCampusPermission
from app.schemas.common import Page
from app.schemas.user import UserCampusesIn, UserCreate, UserDetailOut, UserUpdate
from app.services import audit

router = APIRouter(prefix="/users", tags=["users"])


def _detail(db: Session, user: User) -> UserDetailOut:
    ids = list(db.scalars(select(UserCampusPermission.campus_id).where(UserCampusPermission.user_id == user.id)))
    out = UserDetailOut.model_validate(user)
    out.campus_ids = ids
    return out


def _get(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise not_found("User")
    return user


def _check_password(password: str, username: str) -> None:
    try:
        validate_password_policy(password, username)
    except PasswordPolicyError as exc:
        raise ApiError(422, "weak_password", str(exc), [{"field": "password", "message": str(exc)}]) from exc


def _check_campuses_exist(db: Session, ids: list[uuid.UUID]) -> None:
    if not ids:
        return
    found = set(db.scalars(select(Campus.id).where(Campus.id.in_(ids), Campus.deleted_at.is_(None))))
    missing = set(ids) - found
    if missing:
        raise ApiError(422, "validation_error", "Unknown campus id(s)", [{"field": "campus_ids", "message": str(sorted(map(str, missing)))}])


def _active_admin_count(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.role == Role.ADMIN, User.is_active.is_(True), User.deleted_at.is_(None))
    ) or 0


@router.get("", response_model=Page[UserDetailOut])
def list_users(
    _: AdminUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[UserDetailOut]:
    stmt = select(User).where(User.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(func.lower(User.username)).offset((page - 1) * page_size).limit(page_size))
    return Page(items=[_detail(db, u) for u in rows], total=total, page=page, page_size=page_size)


@router.post("", response_model=UserDetailOut, status_code=201)
def create_user(payload: UserCreate, request: Request, actor: AdminUser, db: DbSession) -> UserDetailOut:
    _check_password(payload.password, payload.username)
    _check_campuses_exist(db, payload.campus_ids)
    user = User(
        username=payload.username, display_name=payload.display_name, email=payload.email,
        password_hash=hash_password(payload.password), role=payload.role, is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("Username already taken", "username_taken") from exc
    for cid in set(payload.campus_ids):
        db.add(UserCampusPermission(user_id=user.id, campus_id=cid))
    audit.record(db, request, "user.create", actor=actor, entity_type="user", entity_id=user.id,
                 details={"username": user.username, "role": user.role.value})
    db.commit()
    return _detail(db, user)


@router.get("/{user_id}", response_model=UserDetailOut)
def get_user(user_id: uuid.UUID, _: AdminUser, db: DbSession) -> UserDetailOut:
    return _detail(db, _get(db, user_id))


@router.patch("/{user_id}", response_model=UserDetailOut)
def update_user(user_id: uuid.UUID, payload: UserUpdate, request: Request, actor: AdminUser, db: DbSession) -> UserDetailOut:
    user = _get(db, user_id)
    changes: dict[str, object] = {}

    new_role = payload.role if payload.role is not None else user.role
    new_active = payload.is_active if payload.is_active is not None else user.is_active
    losing_admin = user.role == Role.ADMIN and user.is_active and (new_role != Role.ADMIN or not new_active)
    if losing_admin and (user.id == actor.id or _active_admin_count(db) <= 1):
        raise ApiError(409, "last_admin", "You cannot demote or disable yourself or the last active administrator")

    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.email is not None:
        user.email = payload.email or None
    if payload.role is not None and payload.role != user.role:
        changes["role"] = payload.role.value
        user.role = payload.role
    if payload.is_active is not None and payload.is_active != user.is_active:
        changes["is_active"] = payload.is_active
        user.is_active = payload.is_active
    if payload.password is not None:
        _check_password(payload.password, user.username)
        user.password_hash = hash_password(payload.password)
        user.failed_login_count, user.locked_until = 0, None
        changes["password_changed"] = True
    if changes.get("password_changed") or changes.get("is_active") is False:
        db.execute(
            update(RefreshSession).where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
    user.updated_at = utcnow()
    audit.record(db, request, "user.update", actor=actor, entity_type="user", entity_id=user.id, details=changes)
    db.commit()
    return _detail(db, user)


@router.put("/{user_id}/campuses", response_model=UserDetailOut)
def set_user_campuses(user_id: uuid.UUID, payload: UserCampusesIn, request: Request, actor: AdminUser, db: DbSession) -> UserDetailOut:
    user = _get(db, user_id)
    _check_campuses_exist(db, payload.campus_ids)
    db.execute(delete(UserCampusPermission).where(UserCampusPermission.user_id == user.id))
    for cid in set(payload.campus_ids):
        db.add(UserCampusPermission(user_id=user.id, campus_id=cid))
    audit.record(db, request, "user.campuses", actor=actor, entity_type="user", entity_id=user.id,
                 details={"campus_count": len(set(payload.campus_ids))})
    db.commit()
    return _detail(db, user)
