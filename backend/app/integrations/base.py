"""Adapter contract between the application and a concrete NVR integration.

Adapters never touch the database. They take connection parameters, talk to the device and return plain data.
Unsupported or untested operations raise NvrUnsupportedError instead of returning made-up results.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType

from app.models.enums import CapabilityName, CapabilityStatus, StreamKind


class NvrError(Exception):
    """Base class. `message` is safe to show to users and is never allowed to contain credentials."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NvrUnreachableError(NvrError):
    """Network-level failure: timeout, refused, no route, DNS."""


class NvrAuthError(NvrError):
    """The NVR rejected the credentials, or the account is locked."""


class NvrProtocolError(NvrError):
    """The device answered, but not in a way we understand (not a Dahua NVR, unexpected payload)."""


class NvrUnsupportedError(NvrError):
    """This adapter or device does not support the requested operation."""


@dataclass(frozen=True)
class ConnectionParams:
    host: str
    http_port: int
    rtsp_port: int
    username: str
    password: str = field(repr=False)  # repr=False keeps it out of logs and tracebacks


@dataclass(frozen=True)
class DeviceInfo:
    model: str | None
    firmware: str | None
    timezone_code: int | None
    device_time: datetime | None  # NVR local wall-clock time, naive
    utc_offset_minutes: int | None  # derived from device_time vs the real UTC time
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StreamProfile:
    stream: StreamKind
    codec: str | None
    width: int | None
    height: int | None
    bitrate_kbps: int | None
    fps: int | None


@dataclass(frozen=True)
class ChannelInfo:
    channel_number: int  # 1-based, Dahua CGI / RTSP numbering
    name: str
    model: str | None
    profiles: tuple[StreamProfile, ...] = ()


@dataclass(frozen=True)
class CameraProbe:
    channel_number: int
    online: bool
    detail: str


@dataclass(frozen=True)
class CapabilityResult:
    name: CapabilityName
    status: CapabilityStatus
    detail: str = ""


class NvrAdapter(ABC):
    """One instance per operation. Use as a context manager so connections are always released."""

    def __enter__(self) -> "NvrAdapter":
        self.connect()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.disconnect()

    @abstractmethod
    def connect(self) -> None:
        """Open the session and prove the credentials work. Raises NvrUnreachableError / NvrAuthError."""

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_device_info(self) -> DeviceInfo: ...

    @abstractmethod
    def get_capabilities(self) -> list[CapabilityResult]:
        """Probe what this NVR can do right now. Only operations observed working may be SUPPORTED_TESTED."""

    @abstractmethod
    def list_cameras(self) -> list[ChannelInfo]:
        """Channels that have a camera configured, with their stream profiles."""

    @abstractmethod
    def get_camera_status(self, channel_number: int) -> CameraProbe:
        """Cheap liveness probe for one channel (does not pull media)."""

    # Phase 3 / Phase 4 operations. Adapters that cannot do them yet raise NvrUnsupportedError.
    def search_recordings(self, channel_number: int, start: datetime, end: datetime) -> list[object]:
        raise NvrUnsupportedError("Recording search is not implemented in this phase")

    def download_recording(self, recording_id: str, destination: str) -> str:
        raise NvrUnsupportedError("Recording download is not implemented in this phase")

    def open_live_stream(self, channel_number: int, stream: StreamKind) -> str:
        raise NvrUnsupportedError("Live streaming is not implemented in this phase")

    def close_live_stream(self, handle: str) -> None:
        raise NvrUnsupportedError("Live streaming is not implemented in this phase")
