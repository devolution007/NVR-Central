import uuid

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.core.errors import conflict, not_found
from app.db.base import utcnow
from app.models.streaming import SavedLayout
from app.schemas.streaming import LayoutIn, LayoutOut

router = APIRouter(prefix="/layouts", tags=["layouts"])

MAX_LAYOUTS_PER_USER = 50


def _out(layout: SavedLayout) -> LayoutOut:
    return LayoutOut(
        id=layout.id, name=layout.name, grid_size=layout.grid_size,
        camera_ids=[uuid.UUID(str(c)) for c in layout.camera_ids], updated_at=layout.updated_at,
    )  # fmt: skip


def _mine(db: DbSession, user: CurrentUser, layout_id: uuid.UUID) -> SavedLayout:
    layout = db.get(SavedLayout, layout_id)
    if layout is None or layout.user_id != user.id:
        raise not_found("Layout")
    return layout


@router.get("", response_model=list[LayoutOut])
def list_layouts(user: CurrentUser, db: DbSession) -> list[LayoutOut]:
    rows = db.scalars(select(SavedLayout).where(SavedLayout.user_id == user.id).order_by(func.lower(SavedLayout.name)))
    return [_out(r) for r in rows]


@router.post("", response_model=LayoutOut, status_code=201)
def create_layout(payload: LayoutIn, user: CurrentUser, db: DbSession) -> LayoutOut:
    count = db.scalar(select(func.count()).select_from(SavedLayout).where(SavedLayout.user_id == user.id)) or 0
    if count >= MAX_LAYOUTS_PER_USER:
        raise conflict(f"You can keep at most {MAX_LAYOUTS_PER_USER} saved layouts", "layout_limit")
    layout = SavedLayout(user_id=user.id, name=payload.name, grid_size=payload.grid_size,
                         camera_ids=[str(c) for c in payload.camera_ids])  # fmt: skip
    db.add(layout)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("You already have a layout with this name", "layout_name_taken") from exc
    return _out(layout)


@router.put("/{layout_id}", response_model=LayoutOut)
def update_layout(layout_id: uuid.UUID, payload: LayoutIn, user: CurrentUser, db: DbSession) -> LayoutOut:
    layout = _mine(db, user, layout_id)
    layout.name, layout.grid_size = payload.name, payload.grid_size
    layout.camera_ids = [str(c) for c in payload.camera_ids]
    layout.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("You already have a layout with this name", "layout_name_taken") from exc
    return _out(layout)


@router.delete("/{layout_id}", status_code=204)
def delete_layout(layout_id: uuid.UUID, user: CurrentUser, db: DbSession, request: Request) -> None:
    db.delete(_mine(db, user, layout_id))
    db.commit()
