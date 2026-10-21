from fastapi import APIRouter, Query
from sqlalchemy import func, select, text

from app.api.deps import AdminUser, CurrentUser, DbSession, allowed_campus_ids
from app.models.audit import AuditLog
from app.models.camera import Camera, CameraBackupPolicy
from app.models.campus import Campus
from app.models.enums import CameraStatus, NvrStatus
from app.models.nvr import Nvr
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page

router = APIRouter()


@router.get("/health", tags=["system"])
def health(db: DbSession) -> dict[str, str]:
    db.execute(text("select 1"))
    return {"status": "ok"}


@router.get("/dashboard/summary", tags=["dashboard"])
def dashboard_summary(user: CurrentUser, db: DbSession) -> dict[str, object]:
    allowed = allowed_campus_ids(db, user)

    campuses = select(func.count()).select_from(Campus).where(Campus.deleted_at.is_(None))
    nvrs = select(Nvr.status, func.count()).where(Nvr.deleted_at.is_(None)).group_by(Nvr.status)
    cameras = select(func.count()).select_from(Camera).where(Camera.status != CameraStatus.UNAVAILABLE)
    backups = (
        select(func.count()).select_from(CameraBackupPolicy)
        .join(Camera, Camera.id == CameraBackupPolicy.camera_id).where(CameraBackupPolicy.enabled.is_(True))
    )
    if allowed is not None:
        campuses = campuses.where(Campus.id.in_(allowed))
        nvrs = nvrs.where(Nvr.campus_id.in_(allowed))
        cameras = cameras.where(Camera.campus_id.in_(allowed))
        backups = backups.where(Camera.campus_id.in_(allowed))

    by_status = {s.value: 0 for s in NvrStatus}
    for status, count in db.execute(nvrs):
        by_status[status.value] = count
    return {
        "campuses": db.scalar(campuses) or 0,
        "nvrs_total": sum(by_status.values()),
        "nvrs_by_status": by_status,
        "cameras": db.scalar(cameras) or 0,
        "cameras_with_backup": db.scalar(backups) or 0,
    }


@router.get("/audit-logs", response_model=Page[AuditLogOut], tags=["audit"])
def list_audit_logs(
    _: AdminUser,
    db: DbSession,
    action: str | None = Query(default=None, max_length=64),
    actor: str | None = Query(default=None, max_length=64),
    success: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[AuditLogOut]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if actor:
        stmt = stmt.where(AuditLog.actor_username == actor.lower())
    if success is not None:
        stmt = stmt.where(AuditLog.success.is_(success))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return Page(items=[AuditLogOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)
