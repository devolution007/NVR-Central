import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import (
    BackupFileStatus,
    BackupJobStatus,
    BackupStream,
    BackupTrigger,
    RecordingMode,
    SftpAuthType,
    StreamKind,
)

_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
_FP_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


# ------------------------------------------------------------------------------------------------ destinations
class HostKeyProbe(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def _valid_host(cls, v: str) -> str:
        v = v.strip()
        if not _HOST_RE.match(v):
            raise ValueError("host must be an IP address or hostname without scheme, port or path")
        return v


class HostKeyOut(BaseModel):
    key_type: str
    sha256: str


class DestinationBase(HostKeyProbe):
    name: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    auth_type: SftpAuthType = SftpAuthType.PASSWORD
    base_path: str = Field(default="/", min_length=1, max_length=512)
    # The fingerprint the administrator confirmed (from the host-key probe or their own records).
    host_key_sha256: str
    host_key_type: str | None = Field(default=None, max_length=32)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("host_key_sha256")
    @classmethod
    def _valid_fingerprint(cls, v: str) -> str:
        v = v.strip()
        if not _FP_RE.match(v):
            raise ValueError("host key must be an OpenSSH SHA256 fingerprint, like SHA256:abc... (43 characters)")
        return v

    @field_validator("base_path")
    @classmethod
    def _clean_base(cls, v: str) -> str:
        v = v.strip().replace("\\", "/")
        if ".." in v.split("/"):
            raise ValueError("base path may not contain '..'")
        return "/" + v.strip("/") if v.strip("/") else "/"


class DestinationCreate(DestinationBase):
    password: str | None = Field(default=None, min_length=1, max_length=256)
    private_key: str | None = Field(default=None, min_length=20, max_length=16000)

    @model_validator(mode="after")
    def _credentials_present(self) -> "DestinationCreate":
        if self.auth_type == SftpAuthType.PASSWORD and not self.password:
            raise ValueError("password is required for password login")
        if self.auth_type == SftpAuthType.KEY and not self.private_key:
            raise ValueError("private_key is required for key login")
        return self


class DestinationUpdate(DestinationBase):
    # None keeps the stored secret. Secrets are write-only.
    password: str | None = Field(default=None, min_length=1, max_length=256)
    private_key: str | None = Field(default=None, min_length=20, max_length=16000)


class DestinationOut(BaseModel):
    id: uuid.UUID
    name: str
    host: str
    port: int
    username: str
    auth_type: SftpAuthType
    has_secret: bool
    base_path: str
    host_key_type: str | None
    host_key_sha256: str | None
    enabled: bool
    last_test_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None
    policy_count: int = 0
    created_at: datetime


# ---------------------------------------------------------------------------------------------------- policies
class PolicyFields(BaseModel):
    enabled: bool | None = None
    backup_stream: BackupStream | None = None
    frequency_minutes: int | None = Field(default=None, ge=5, le=1440)
    recording_mode: RecordingMode | None = None
    destination_id: uuid.UUID | None = None
    # When a policy is switched on for the first time: how far back to start (0 = only new recordings).
    backfill_hours: int | None = Field(default=None, ge=0, le=168)


class PolicyUpdate(PolicyFields):
    enabled: bool = False
    backup_stream: BackupStream = BackupStream.SUB
    frequency_minutes: int = Field(default=60, ge=5, le=1440)
    recording_mode: RecordingMode = RecordingMode.ALL
    backfill_hours: int = Field(default=0, ge=0, le=168)


class PolicyBulk(PolicyFields):
    camera_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class PolicyOut(BaseModel):
    camera_id: uuid.UUID
    camera_name: str
    channel_number: int
    nvr_id: uuid.UUID
    nvr_name: str
    campus_id: uuid.UUID
    campus_name: str
    configured: bool  # False: the camera has no policy yet (defaults shown)
    enabled: bool
    backup_stream: BackupStream
    frequency_minutes: int
    recording_mode: RecordingMode
    destination_id: uuid.UUID | None
    destination_name: str | None
    checkpoint_at: datetime | None
    last_run_at: datetime | None
    next_run_at: datetime | None
    consecutive_failures: int
    last_job_status: BackupJobStatus | None
    last_job_message: str | None


# -------------------------------------------------------------------------------------------------- recordings
class RecordingOut(BaseModel):
    start: datetime  # UTC
    end: datetime  # UTC
    stream: StreamKind
    size_bytes: int | None
    kind: str
    in_progress: bool
    backup_status: BackupFileStatus | None
    backup_file_id: uuid.UUID | None
    remote_path: str | None


class RecordingSearchOut(BaseModel):
    camera_id: uuid.UUID
    searched_from: datetime
    searched_to: datetime
    utc_offset_minutes: int
    items: list[RecordingOut]


# --------------------------------------------------------------------------------------------------------- jobs
class JobCreate(BaseModel):
    camera_id: uuid.UUID
    stream: BackupStream | None = None  # default: the camera's policy, else sub
    destination_id: uuid.UUID | None = None  # default: the camera's policy destination
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _window(self) -> "JobCreate":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start and end must include a timezone (use UTC, e.g. 2026-10-22T08:00:00Z)")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class JobOut(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    camera_name: str
    channel_number: int
    nvr_id: uuid.UUID
    nvr_name: str
    campus_id: uuid.UUID
    campus_name: str
    destination_id: uuid.UUID
    destination_name: str
    stream: BackupStream
    trigger: BackupTrigger
    status: BackupJobStatus
    window_start: datetime
    window_end: datetime
    cancel_requested: bool
    files_total: int
    files_done: int
    files_failed: int
    files_skipped: int
    bytes_total: int
    bytes_done: int
    message: str | None
    requested_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class FileOut(BaseModel):
    id: uuid.UUID
    status: BackupFileStatus
    recording_start: datetime
    recording_end: datetime
    size_bytes: int | None
    bytes_done: int
    attempts: int
    error: str | None
    remote_path: str | None
    sha256: str | None


class JobDetail(JobOut):
    files: list[FileOut]


class BackupStatusOut(BaseModel):
    worker_online: bool | None  # None: could not tell (queue unreachable)
    detail: str
    active_jobs: int
    queued_jobs: int
    failed_last_24h: int
    bytes_last_24h: int
    destinations_failing: int
    staging_free_gb: float | None
