import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import PeerMode


class HubOut(BaseModel):
    configured: bool
    public_key: str | None
    endpoint: str | None
    network: str
    hub_address: str
    notes: list[str]


class PeerCreate(BaseModel):
    campus_id: uuid.UUID
    name: str = Field(min_length=1, max_length=128)
    mode: PeerMode = PeerMode.CONNECTOR
    # The campus LAN as it really is, e.g. 192.168.15.0/24. Optional for Windows mode (derived from nvr_host).
    lan_cidr: str | None = Field(default=None, max_length=18)
    # Optional: supply the public key of a peer that generated its own keypair (recommended for routers).
    public_key: str | None = Field(default=None, max_length=64)
    # Windows mode only: the NVR's real LAN address and ports, used to build the port-forwarding script.
    nvr_host: str | None = Field(default=None, max_length=15)
    nvr_http_port: int = Field(default=80, ge=1, le=65535)
    nvr_rtsp_port: int = Field(default=554, ge=1, le=65535)


class PeerOut(BaseModel):
    id: uuid.UUID
    campus_id: uuid.UUID
    campus_name: str
    name: str
    mode: PeerMode
    public_key: str
    tunnel_ip: str
    virtual_subnet: str
    lan_cidr: str
    nvr_address_pattern: str
    status: str  # connected | stale | never_connected | unknown | revoked
    last_handshake_at: datetime | None
    health_checked_at: datetime | None
    rx_bytes: int | None
    tx_bytes: int | None
    created_at: datetime


class PeerCreated(BaseModel):
    peer: PeerOut
    # Shown once and not retrievable later. `wg_quick` is a ready config file containing the generated private key;
    # `settings` is a sheet for peers that made their own key and still need the pre-shared key.
    config: str
    config_kind: str  # wg_quick | settings
    # Windows mode: PowerShell that forwards the NVR's ports from the tunnel address. Run as Administrator.
    script: str | None = None
    script_name: str | None = None
    private_key_shown_once: bool
    warnings: list[str]
