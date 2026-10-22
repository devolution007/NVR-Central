import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import CameraStatus, CapabilityName, CapabilityStatus, StreamKind


class StreamProfileOut(BaseModel):
    stream: StreamKind
    codec: str | None
    width: int | None
    height: int | None
    bitrate_kbps: int | None
    fps: int | None


class CameraOut(BaseModel):
    id: uuid.UUID
    nvr_id: uuid.UUID
    nvr_name: str
    campus_id: uuid.UUID
    campus_name: str
    channel_number: int
    name: str
    model: str | None
    status: CameraStatus
    status_detail: str | None
    status_checked_at: datetime | None
    last_discovered_at: datetime | None
    main_stream: StreamProfileOut | None
    sub_stream: StreamProfileOut | None
    is_favorite: bool = False


class CameraStatusOut(BaseModel):
    """The last stored probe result. Not a live check: `checked_at` says how old it is."""

    status: CameraStatus
    detail: str | None
    checked_at: datetime | None


class CapabilityOut(BaseModel):
    name: CapabilityName
    status: CapabilityStatus
    detail: str
    checked_at: datetime | None


class OperationQueued(BaseModel):
    nvr_id: uuid.UUID
    operation: str
    status: str = "queued"
