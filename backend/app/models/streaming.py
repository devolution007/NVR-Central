import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, utcnow
from app.models.enums import StreamKind, StreamSessionStatus


def _enum(e: type) -> SAEnum:
    return SAEnum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class StreamSession(Base, IdMixin):
    """One viewer watching one camera stream. The HLS URL carries this id; every request re-checks it."""

    __tablename__ = "stream_sessions"

    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cameras.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    stream: Mapped[StreamKind] = mapped_column(_enum(StreamKind))
    path_name: Mapped[str] = mapped_column(String(96))  # the MediaMTX path serving this session
    transcoded: Mapped[bool] = mapped_column(default=False)
    status: Mapped[StreamSessionStatus] = mapped_column(_enum(StreamSessionStatus), default=StreamSessionStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ip_address: Mapped[str | None] = mapped_column(String(64), default=None)

    __table_args__ = (Index("ix_stream_sessions_active", "status", "expires_at"),)


class CameraFavorite(Base):
    __tablename__ = "camera_favorites"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SavedLayout(Base, IdMixin, TimestampMixin):
    """A named set of cameras and grid size for one user (spec: saved custom viewing layouts)."""

    __tablename__ = "saved_camera_layouts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    grid_size: Mapped[int] = mapped_column(Integer, default=4)
    # Ordered camera ids. Ids of cameras that later disappear or lose access are skipped when the layout is opened.
    camera_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)

    __table_args__ = (Index("uq_saved_layouts_user_name", "user_id", func.lower(name), unique=True),)
