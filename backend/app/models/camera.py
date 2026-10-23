import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.models.enums import BackupStream, CameraStatus, RecordingMode, StreamKind


def _enum(e: type) -> SAEnum:
    return SAEnum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class Camera(Base, IdMixin, TimestampMixin):
    """A channel on an NVR. Populated by discovery in Phase 2; never deleted when a camera disappears."""

    __tablename__ = "cameras"

    nvr_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nvrs.id"), index=True)
    campus_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campuses.id"), index=True)
    # 1-based, matching Dahua CGI search and RTSP numbering (ChannelTitle[] is 0-based).
    channel_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[CameraStatus] = mapped_column(_enum(CameraStatus), default=CameraStatus.UNKNOWN)
    model: Mapped[str | None] = mapped_column(String(64), default=None)
    last_discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    status_detail: Mapped[str | None] = mapped_column(String(256), default=None)

    __table_args__ = (UniqueConstraint("nvr_id", "channel_number", name="uq_cameras_nvr_channel"),)


class CameraStreamProfile(Base, IdMixin):
    """Encode settings the NVR reports for a camera's main and sub streams."""

    __tablename__ = "camera_stream_profiles"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), index=True
    )
    stream: Mapped[StreamKind] = mapped_column(_enum(StreamKind))
    codec: Mapped[str | None] = mapped_column(String(16), default=None)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer, default=None)
    fps: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (UniqueConstraint("camera_id", "stream", name="uq_camera_stream_profiles_camera_stream"),)


class CameraBackupPolicy(Base, IdMixin, TimestampMixin):
    """Per-camera automatic backup settings. API and engine arrive in Phase 4."""

    __tablename__ = "camera_backup_policies"

    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cameras.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Substream by default: roughly 232 MB/hour versus 908 MB/hour for main on the test NVR.
    backup_stream: Mapped[BackupStream] = mapped_column(_enum(BackupStream), default=BackupStream.SUB)
    frequency_minutes: Mapped[int] = mapped_column(Integer, default=60)
    recording_mode: Mapped[RecordingMode] = mapped_column(_enum(RecordingMode), default=RecordingMode.ALL)
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sftp_destinations.id"), default=None
    )
    # Everything recorded before this instant (UTC) is already safely backed up. The next run starts a little earlier
    # (overlap) so a recording that was still being written last time is picked up now.
    checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
