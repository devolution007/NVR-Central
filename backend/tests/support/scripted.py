"""SIMULATED adapter whose behaviour the test scripts directly (no network). Test use only."""

from collections.abc import Callable
from datetime import datetime

from app.integrations.base import (
    CameraProbe,
    CapabilityResult,
    ChannelInfo,
    ConnectionParams,
    DeviceInfo,
    NvrAdapter,
    StreamProfile,
)
from app.models.enums import CapabilityName, CapabilityStatus, StreamKind
from app.models.nvr import Nvr


def profile(stream: StreamKind) -> StreamProfile:
    return StreamProfile(stream, "H.265", 1920 if stream == StreamKind.MAIN else 704, 1080 if stream == StreamKind.MAIN else 576, 2048, 25)


def channel(n: int, name: str | None = None, model: str | None = "DH-SIM") -> ChannelInfo:
    return ChannelInfo(n, name or f"Camera {n}", model, (profile(StreamKind.MAIN), profile(StreamKind.SUB)))


def all_caps(**overrides: CapabilityStatus) -> list[CapabilityResult]:
    out = []
    for name in CapabilityName:
        status = overrides.get(name.value, CapabilityStatus.SUPPORTED_TESTED)
        out.append(CapabilityResult(name, status, f"scripted {status.value}"))
    return out


class ScriptedAdapter(NvrAdapter):
    """Configure by attribute. Set `raise_on_connect` to make connect() raise that exception."""

    def __init__(self) -> None:
        self.raise_on_connect: Exception | None = None
        self.connect_calls = 0
        self.info = DeviceInfo("DHI-SIM-NVR", "1.0", 7, datetime(2026, 1, 1, 12, 0), 300)
        self.channels: list[ChannelInfo] = [channel(1), channel(2)]
        self.capabilities = all_caps(p2p_auth=CapabilityStatus.UNKNOWN, recording_download=CapabilityStatus.UNKNOWN)
        self.offline_channels: set[int] = set()
        self.listed = 0

    def connect(self) -> None:
        self.connect_calls += 1
        if self.raise_on_connect is not None:
            raise self.raise_on_connect

    def disconnect(self) -> None:
        return None

    def get_device_info(self) -> DeviceInfo:
        return self.info

    def get_capabilities(self) -> list[CapabilityResult]:
        return self.capabilities

    def list_cameras(self) -> list[ChannelInfo]:
        self.listed += 1
        return list(self.channels)

    def get_camera_status(self, channel_number: int) -> CameraProbe:
        online = channel_number not in self.offline_channels
        return CameraProbe(channel_number, online, "scripted online" if online else "scripted offline")


def builder_for(adapter: ScriptedAdapter) -> Callable[[Nvr, ConnectionParams], NvrAdapter]:
    return lambda nvr, params: adapter
