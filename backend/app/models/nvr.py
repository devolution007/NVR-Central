import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin
from app.models.enums import CapabilityName, CapabilityStatus, ConnectionType, NvrStatus


def _enum(e: type) -> SAEnum:
    return SAEnum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class Nvr(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "nvrs"

    campus_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campuses.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))

    # --- connection profile ---
    connection_type: Mapped[ConnectionType] = mapped_column(_enum(ConnectionType))
    host: Mapped[str | None] = mapped_column(String(253), default=None)
    http_port: Mapped[int] = mapped_column(Integer, default=80)
    rtsp_port: Mapped[int] = mapped_column(Integer, default=554)
    username: Mapped[str] = mapped_column(String(64))
    # Fernet ciphertext. Never returned by the API and never logged.
    password_encrypted: Mapped[str] = mapped_column(Text)
    p2p_serial_encrypted: Mapped[str | None] = mapped_column(Text, default=None)

    # --- discovered / operational state ---
    status: Mapped[NvrStatus] = mapped_column(_enum(NvrStatus), default=NvrStatus.PENDING)
    status_message: Mapped[str | None] = mapped_column(String(512), default=None)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    model: Mapped[str | None] = mapped_column(String(64), default=None)
    firmware: Mapped[str | None] = mapped_column(String(64), default=None)
    # Dahua NTP.TimeZone enum value, needed to convert NVR local timestamps to UTC (Phase 2/4).
    timezone_code: Mapped[int | None] = mapped_column(Integer, default=None)
    # NVR local time minus UTC, measured from the NVR clock at the last successful check (15-minute steps).
    utc_offset_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Most simultaneous live streams the server opens to this NVR. None = the server-wide MAX_STREAMS_PER_NVR default.
    max_live_streams: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        Index(
            "uq_nvrs_campus_name_active",
            "campus_id",
            func.lower(name),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class NvrCapability(Base, IdMixin):
    __tablename__ = "nvr_capabilities"

    nvr_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nvrs.id", ondelete="CASCADE"), index=True)
    name: Mapped[CapabilityName] = mapped_column(_enum(CapabilityName))
    status: Mapped[CapabilityStatus] = mapped_column(_enum(CapabilityStatus), default=CapabilityStatus.UNKNOWN)
    detail: Mapped[str] = mapped_column(String(512), default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (UniqueConstraint("nvr_id", "name", name="uq_nvr_capabilities_nvr_name"),)
