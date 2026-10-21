import uuid

from fastapi import APIRouter, Query, Request
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession, allowed_campus_ids, assert_campus_access
from app.core.errors import ApiError, conflict, not_found
from app.db.base import utcnow
from app.models.camera import Camera, CameraBackupPolicy
from app.models.campus import Campus
from app.models.enums import CameraStatus, NvrStatus
from app.models.nvr import Nvr
from app.schemas.campus import CampusCreate, CampusOut, CampusUpdate
from app.schemas.common import Page
from app.services import audit

router = APIRouter(prefix="/campuses", tags=["campuses"])


def _stats(db: Session, campus_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    stats = {cid: {"nvr_count": 0, "connected_nvr_count": 0, "camera_count": 0, "backup_enabled_camera_count": 0} for cid in campus_ids}
    if not campus_ids:
        return stats
    for cid, total, connected in db.execute(
        select(
            Nvr.campus_id,
            func.count(),
            func.count().filter(Nvr.status == NvrStatus.CONNECTED),
        )
        .where(Nvr.campus_id.in_(campus_ids), Nvr.deleted_at.is_(None))
        .group_by(Nvr.campus_id)
    ):
        stats[cid]["nvr_count"] = total
        stats[cid]["connected_nvr_count"] = connected
    for cid, total in db.execute(
        select(Camera.campus_id, func.count())
        .where(Camera.campus_id.in_(campus_ids), Camera.status != CameraStatus.UNAVAILABLE)
        .group_by(Camera.campus_id)
    ):
        stats[cid]["camera_count"] = total
    for cid, total in db.execute(
        select(Camera.campus_id, func.count())
        .join(CameraBackupPolicy, CameraBackupPolicy.camera_id == Camera.id)
        .where(Camera.campus_id.in_(campus_ids), CameraBackupPolicy.enabled.is_(True))
        .group_by(Camera.campus_id)
    ):
        stats[cid]["backup_enabled_camera_count"] = total
    return stats


def _out(campus: Campus, stats: dict[str, int]) -> CampusOut:
    out = CampusOut.model_validate(campus)
    for k, v in stats.items():
        setattr(out, k, v)
    return out


def _get_active(db: Session, campus_id: uuid.UUID) -> Campus:
    campus = db.get(Campus, campus_id)
    if campus is None or campus.deleted_at is not None:
        raise not_found("Campus")
    return campus


@router.get("", response_model=Page[CampusOut])
def list_campuses(
    user: CurrentUser,
    db: DbSession,
    q: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[CampusOut]:
    stmt: Select[tuple[Campus]] = select(Campus).where(Campus.deleted_at.is_(None))
    allowed = allowed_campus_ids(db, user)
    if allowed is not None:
        stmt = stmt.where(Campus.id.in_(allowed))
    if q:
        stmt = stmt.where(Campus.name.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(func.lower(Campus.name)).offset((page - 1) * page_size).limit(page_size)))
    stats = _stats(db, [c.id for c in rows])
    return Page(items=[_out(c, stats[c.id]) for c in rows], total=total, page=page, page_size=page_size)


@router.post("", response_model=CampusOut, status_code=201)
def create_campus(payload: CampusCreate, request: Request, user: AdminUser, db: DbSession) -> CampusOut:
    campus = Campus(**payload.model_dump())
    db.add(campus)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("A campus with this name already exists", "campus_name_taken") from exc
    audit.record(db, request, "campus.create", actor=user, entity_type="campus", entity_id=campus.id, campus_id=campus.id,
                 details={"name": campus.name})
    db.commit()
    return _out(campus, _stats(db, [campus.id])[campus.id])


@router.get("/{campus_id}", response_model=CampusOut)
def get_campus(campus_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CampusOut:
    assert_campus_access(db, user, campus_id)
    campus = _get_active(db, campus_id)
    return _out(campus, _stats(db, [campus.id])[campus.id])


@router.put("/{campus_id}", response_model=CampusOut)
def update_campus(campus_id: uuid.UUID, payload: CampusUpdate, request: Request, user: AdminUser, db: DbSession) -> CampusOut:
    campus = _get_active(db, campus_id)
    campus.name, campus.description, campus.location = payload.name, payload.description, payload.location
    campus.updated_at = utcnow()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("A campus with this name already exists", "campus_name_taken") from exc
    audit.record(db, request, "campus.update", actor=user, entity_type="campus", entity_id=campus.id, campus_id=campus.id,
                 details={"name": campus.name})
    db.commit()
    return _out(campus, _stats(db, [campus.id])[campus.id])


@router.delete("/{campus_id}", status_code=204)
def delete_campus(campus_id: uuid.UUID, request: Request, user: AdminUser, db: DbSession) -> None:
    campus = _get_active(db, campus_id)
    nvrs = db.scalar(select(func.count()).select_from(Nvr).where(Nvr.campus_id == campus.id, Nvr.deleted_at.is_(None))) or 0
    if nvrs:
        raise ApiError(409, "campus_has_nvrs", f"Remove or move the {nvrs} NVR(s) in this campus first")
    campus.deleted_at = utcnow()
    audit.record(db, request, "campus.delete", actor=user, entity_type="campus", entity_id=campus.id, campus_id=campus.id,
                 details={"name": campus.name})
    db.commit()
