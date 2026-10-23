"""Dahua P2P adapter placeholder.

Direct P2P is disabled on purpose: both open-source libraries failed against the test NVR (see
docs/p2p-compatibility.md). This class exists so the factory has a defined, honest answer for p2p connections,
and so a verified implementation can replace it later without touching callers.
"""

from datetime import datetime

from app.integrations.base import (
    CameraProbe,
    CapabilityResult,
    ChannelInfo,
    ConnectionParams,
    DeviceInfo,
    NvrAdapter,
    NvrUnsupportedError,
    RecordingFile,
)

P2P_DISABLED = (
    "Direct P2P is disabled: it did not connect to the test NVR. Register this NVR with a campus tunnel or LAN "
    "address instead."
)


class DahuaP2pAdapter(NvrAdapter):
    def __init__(self, params: ConnectionParams | None = None) -> None:
        self._params = params

    def connect(self) -> None:
        raise NvrUnsupportedError(P2P_DISABLED)

    def disconnect(self) -> None:
        return None

    def get_device_info(self) -> DeviceInfo:
        raise NvrUnsupportedError(P2P_DISABLED)

    def get_capabilities(self) -> list[CapabilityResult]:
        raise NvrUnsupportedError(P2P_DISABLED)

    def list_cameras(self) -> list[ChannelInfo]:
        raise NvrUnsupportedError(P2P_DISABLED)

    def get_camera_status(self, channel_number: int) -> CameraProbe:
        raise NvrUnsupportedError(P2P_DISABLED)

    def search_recordings(self, channel_number: int, start: datetime, end: datetime) -> list[RecordingFile]:
        raise NvrUnsupportedError(P2P_DISABLED)
