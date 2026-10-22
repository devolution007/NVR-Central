import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import StreamKind

GridSize = Literal[1, 4, 9, 16, 25]


class StreamStart(BaseModel):
    stream: StreamKind = StreamKind.SUB
    # The browser can play H.265 itself, so the server need not convert it. Decided by the client from what it supports.
    native_hevc: bool = False


class LiveInfoOut(BaseModel):
    """What the UI needs before it tries to play: whether it can, why not, and how much room the NVR has."""

    can_stream: bool
    reason: str | None
    stream: StreamKind
    codec: str | None
    transcode: bool
    nvr_active_streams: int
    nvr_limit: int


class StreamSessionOut(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    stream: StreamKind
    # Same-origin URL served by the backend after it checks the viewer's session. It never contains NVR credentials.
    hls_url: str
    transcoded: bool
    expires_at: datetime
    keepalive_every_seconds: int


class ActiveStreamOut(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    camera_name: str
    nvr_name: str
    campus_name: str
    stream: StreamKind
    username: str
    transcoded: bool
    created_at: datetime
    expires_at: datetime


class StreamingStatusOut(BaseModel):
    gateway_reachable: bool
    active_sessions: int
    max_streams_per_nvr: int
    max_streams_total: int
    transcode_mode: str


class LayoutIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    grid_size: GridSize = 4
    camera_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class LayoutOut(BaseModel):
    id: uuid.UUID
    name: str
    grid_size: int
    camera_ids: list[uuid.UUID]
    updated_at: datetime
