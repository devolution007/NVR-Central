import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import ConnectionType, NvrStatus

_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


class NvrBase(BaseModel):
    campus_id: uuid.UUID
    name: str = Field(min_length=1, max_length=128)
    connection_type: ConnectionType
    host: str | None = Field(default=None, max_length=253)
    http_port: int = Field(default=80, ge=1, le=65535)
    rtsp_port: int = Field(default=554, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=64)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("host")
    @classmethod
    def _valid_host(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.strip()
        if not _HOST_RE.match(v):
            raise ValueError("host must be an IP address or hostname without scheme, port or path")
        return v


class NvrCreate(NvrBase):
    password: str = Field(min_length=1, max_length=128)
    p2p_serial: str | None = Field(default=None, min_length=4, max_length=64)

    @model_validator(mode="after")
    def _profile_complete(self) -> "NvrCreate":
        if self.connection_type in (ConnectionType.TUNNEL, ConnectionType.LAN) and not self.host:
            raise ValueError("host is required for tunnel and lan connections")
        if self.connection_type == ConnectionType.P2P and not self.p2p_serial:
            raise ValueError("p2p_serial is required for p2p connections")
        return self


class NvrUpdate(NvrBase):
    # None keeps the stored value. Secrets are write-only.
    password: str | None = Field(default=None, min_length=1, max_length=128)
    p2p_serial: str | None = Field(default=None, min_length=4, max_length=64)


class NvrOut(BaseModel):
    id: uuid.UUID
    campus_id: uuid.UUID
    campus_name: str
    name: str
    connection_type: ConnectionType
    host: str | None
    http_port: int
    rtsp_port: int
    username: str
    has_password: bool
    p2p_serial_masked: str | None
    status: NvrStatus
    status_message: str | None
    model: str | None
    firmware: str | None
    enabled: bool
    camera_count: int = 0
    created_at: datetime
    updated_at: datetime
