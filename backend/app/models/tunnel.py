import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.models.enums import PeerMode


class TunnelPeer(Base, IdMixin, TimestampMixin):
    """One campus end of a WireGuard tunnel to the central hub.

    The peer private key is never stored: the server shows it once at creation (or the peer supplies its own
    public key). Only the public key and the pre-shared key (the hub needs it to write its config) are kept.
    """

    __tablename__ = "tunnel_peers"

    campus_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campuses.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    mode: Mapped[PeerMode] = mapped_column(
        SAEnum(PeerMode, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e])
    )
    public_key: Mapped[str] = mapped_column(String(64))
    preshared_key_encrypted: Mapped[str] = mapped_column(Text)
    slot: Mapped[int] = mapped_column(Integer)  # 1..253, derives tunnel_ip and virtual_subnet
    tunnel_ip: Mapped[str] = mapped_column(String(15))  # 10.77.0.<slot+1>
    virtual_subnet: Mapped[str] = mapped_column(
        String(18)
    )  # 10.77.<slot>.0/24: how the server addresses the campus LAN
    lan_cidr: Mapped[str] = mapped_column(String(18))  # the campus LAN as it really is, e.g. 192.168.15.0/24

    last_handshake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    rx_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    tx_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("uq_tunnel_peers_slot_active", "slot", unique=True, postgresql_where=text("revoked_at IS NULL")),
        Index("uq_tunnel_peers_pubkey_active", "public_key", unique=True, postgresql_where=text("revoked_at IS NULL")),
    )
