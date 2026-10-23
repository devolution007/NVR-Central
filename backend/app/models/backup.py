import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin
from app.models.enums import (
    BackupFileStatus,
    BackupJobStatus,
    BackupStream,
    BackupTrigger,
    SftpAuthType,
)


def _enum(e: type) -> SAEnum:
    return SAEnum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class SftpDestination(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    """Where backups are uploaded. Managed by administrators; credentials are encrypted and never returned."""

    __tablename__ = "sftp_destinations"

    name: Mapped[str] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(128))
    auth_type: Mapped[SftpAuthType] = mapped_column(_enum(SftpAuthType))
    password_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    private_key_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    base_path: Mapped[str] = mapped_column(String(512), default="/")
    # SHA-256 fingerprint of the server's host key (OpenSSH style "SHA256:..."), pinned by an administrator.
    # Connections are refused when the server presents anything else, and when nothing is pinned yet.
    host_key_type: Mapped[str | None] = mapped_column(String(32), default=None)
    host_key_sha256: Mapped[str | None] = mapped_column(String(128), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    last_test_message: Mapped[str | None] = mapped_column(String(512), default=None)

    __table_args__ = (
        Index(
            "uq_sftp_destinations_name_active",
            func.lower(name),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class BackupJob(Base, IdMixin, TimestampMixin):
    """One run of the backup engine for one camera and one time window."""

    __tablename__ = "backup_jobs"

    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cameras.id"), index=True)
    nvr_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nvrs.id"), index=True)
    campus_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campuses.id"), index=True)
    destination_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sftp_destinations.id"))
    stream: Mapped[BackupStream] = mapped_column(_enum(BackupStream))
    trigger: Mapped[BackupTrigger] = mapped_column(_enum(BackupTrigger))
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), default=None)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC
    status: Mapped[BackupJobStatus] = mapped_column(_enum(BackupJobStatus), default=BackupJobStatus.QUEUED, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    files_total: Mapped[int] = mapped_column(Integer, default=0)
    files_done: Mapped[int] = mapped_column(Integer, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0)
    bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_done: Mapped[int] = mapped_column(BigInteger, default=0)
    message: Mapped[str | None] = mapped_column(String(1024), default=None)
    # The worker refreshes this while it works; a running job whose heartbeat stops is recovered by the sweeper.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class BackupFile(Base, IdMixin, TimestampMixin):
    """One NVR recording file and its journey to the SFTP server. A (camera, destination, stream, start) pair is
    backed up once: later jobs find the finished row and skip it (dedup)."""

    __tablename__ = "backup_files"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("backup_jobs.id"), index=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cameras.id"), index=True)
    destination_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sftp_destinations.id"))
    stream: Mapped[BackupStream] = mapped_column(_enum(BackupStream))
    nvr_path: Mapped[str] = mapped_column(String(512))
    recording_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC
    recording_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)  # as reported by the NVR
    status: Mapped[BackupFileStatus] = mapped_column(_enum(BackupFileStatus), default=BackupFileStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    bytes_done: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    remote_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    error: Mapped[str | None] = mapped_column(String(1024), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("uq_backup_files_dedup", "camera_id", "destination_id", "stream", "recording_start", unique=True),
    )
