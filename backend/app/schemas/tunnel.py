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
    lan_cidr: str = Field(
        min_length=7, max_length=18, description="The campus LAN as it really is, e.g. 192.168.15.0/24"
    )
    # Optional: supply the public key of a peer that generated its own keypair (recommended for routers).
    public_key: str | None = Field(default=None, max_length=64)


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
    # Present only when the server generated the keypair. It is shown once and not stored anywhere.
    config: str | None
    private_key_shown_once: bool
    warnings: list[str]
