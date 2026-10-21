from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class ConnectionType(StrEnum):
    """How the central server reaches the NVR.

    TUNNEL: via the campus WireGuard tunnel (primary path, see docs/PHASES.md).
    LAN:    directly reachable address (same network or existing VPN).
    P2P:    Dahua P2P by serial. Experimental; failed hardware testing on the test NVR.
    """

    TUNNEL = "tunnel"
    LAN = "lan"
    P2P = "p2p"


class NvrStatus(StrEnum):
    PENDING = "pending"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    OFFLINE = "offline"
    AUTH_FAILED = "auth_failed"
    P2P_UNAVAILABLE = "p2p_unavailable"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class CameraStatus(StrEnum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    UNAVAILABLE = "unavailable"  # disappeared from the NVR; kept so backup history survives


class BackupStream(StrEnum):
    """Which NVR recording stream a camera backs up. Substream is the default (about 1/4 the size)."""

    SUB = "sub"
    MAIN = "main"


class RecordingMode(StrEnum):
    ALL = "all"
    CONTINUOUS = "continuous"
    MOTION = "motion"
    EVENT = "event"


class StreamKind(StrEnum):
    MAIN = "main"
    SUB = "sub"


class CapabilityName(StrEnum):
    P2P_AUTH = "p2p_auth"
    DEVICE_INFO = "device_info"
    CHANNEL_DISCOVERY = "channel_discovery"
    LIVE_VIDEO = "live_video"
    RECORDING_SEARCH = "recording_search"
    RECORDING_DOWNLOAD = "recording_download"
    SNAPSHOT = "snapshot"


class CapabilityStatus(StrEnum):
    """Never mark something supported because a connection worked. SUPPORTED_TESTED means this exact operation
    was observed working against this NVR; UNKNOWN means we have not tried it."""

    SUPPORTED_TESTED = "supported_tested"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class PeerMode(StrEnum):
    CONNECTOR = "connector"  # small always-on box running our WireGuard + NAT setup
    ROUTER = "router"  # the campus router holds the tunnel (its own WireGuard client)
