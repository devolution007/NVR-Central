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
