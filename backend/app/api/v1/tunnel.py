import uuid

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, DbSession
from app.core.config import get_settings
from app.core.errors import ApiError, not_found
from app.db.base import utcnow
from app.models.campus import Campus
from app.models.tunnel import TunnelPeer
from app.schemas.tunnel import HubOut, PeerCreate, PeerCreated, PeerOut
from app.services import audit, tunnel

router = APIRouter(prefix="/tunnel", tags=["tunnel"])


def _peer_out(db: DbSession, peer: TunnelPeer) -> PeerOut:
    campus = db.get(Campus, peer.campus_id)
    return PeerOut(
        id=peer.id,
        campus_id=peer.campus_id,
        campus_name=campus.name if campus else "",
        name=peer.name,
        mode=peer.mode,
        public_key=peer.public_key,
        tunnel_ip=peer.tunnel_ip,
        virtual_subnet=peer.virtual_subnet,
        lan_cidr=peer.lan_cidr,
        nvr_address_pattern=tunnel.nvr_address_pattern(peer),
        status=tunnel.peer_status(peer),
        last_handshake_at=peer.last_handshake_at,
        health_checked_at=peer.health_checked_at,
        rx_bytes=peer.rx_bytes,
        tx_bytes=peer.tx_bytes,
        created_at=peer.created_at,
    )


@router.get("/hub", response_model=HubOut)
def get_hub(_: AdminUser) -> HubOut:
    s = get_settings()
    notes = []
    if not s.wg_hub_private_key:
        notes.append("WG_HUB_PRIVATE_KEY is not set. Run scripts/init-env.py.")
    if not s.wg_hub_endpoint:
        notes.append("WG_HUB_ENDPOINT is not set. Campuses need the hub's public host:port to connect to.")
    notes.append("Tunnel handshakes are only reported when the worker can run `wg` next to the hub (docs/tunnel.md).")
    return HubOut(
        configured=bool(s.wg_hub_private_key and s.wg_hub_endpoint),
        public_key=tunnel.hub_public_key(),
        endpoint=s.wg_hub_endpoint or None,
        network=tunnel.NETWORK,
        hub_address=tunnel.HUB_IP,
        notes=notes,
    )


@router.get("/peers", response_model=list[PeerOut])
def list_peers(_: AdminUser, db: DbSession, campus_id: uuid.UUID | None = None) -> list[PeerOut]:
    stmt = select(TunnelPeer).where(TunnelPeer.revoked_at.is_(None)).order_by(TunnelPeer.slot)
    if campus_id:
        stmt = stmt.where(TunnelPeer.campus_id == campus_id)
    return [_peer_out(db, p) for p in db.scalars(stmt)]


@router.post("/peers", response_model=PeerCreated, status_code=201)
def create_peer(payload: PeerCreate, request: Request, user: AdminUser, db: DbSession) -> PeerCreated:
    campus = db.get(Campus, payload.campus_id)
    if campus is None or campus.deleted_at is not None:
        raise ApiError(
            422, "validation_error", "Campus does not exist", [{"field": "campus_id", "message": "Unknown campus"}]
        )
    try:
        created = tunnel.create_peer(
            db, campus.id, payload.name.strip(), payload.mode, payload.lan_cidr, payload.public_key, payload.nvr_host
        )
    except tunnel.TunnelError as exc:
        db.rollback()
        raise ApiError(422 if exc.code != "no_free_slot" else 409, exc.code, exc.message) from exc
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "peer_conflict", "Could not allocate the tunnel address, try again") from exc

    audit.record(
        db,
        request,
        "tunnel.peer_create",
        actor=user,
        entity_type="tunnel_peer",
        entity_id=created.peer.id,
        campus_id=campus.id,
        details={"name": created.peer.name, "mode": created.peer.mode.value, "slot": created.peer.slot},
    )
    db.commit()
    tunnel.write_hub_conf(db)
    if created.private_key:
        config, kind = tunnel.peer_config(created.peer, created.private_key), "wg_quick"
    else:
        config, kind = tunnel.peer_settings(created.peer, created.preshared_key), "settings"
    script = script_name = None
    if created.nvr_host:
        ports = sorted({payload.nvr_http_port, payload.nvr_rtsp_port})
        script = tunnel.windows_script(created.peer, created.nvr_host, ports)
        script_name = "nvr-central-forward.ps1"
    return PeerCreated(
        peer=_peer_out(db, created.peer),
        config=config,
        config_kind=kind,
        script=script,
        script_name=script_name,
        private_key_shown_once=created.private_key is not None,
        warnings=created.warnings,
    )


@router.delete("/peers/{peer_id}", status_code=204)
def revoke_peer(peer_id: uuid.UUID, request: Request, user: AdminUser, db: DbSession) -> None:
    peer = db.get(TunnelPeer, peer_id)
    if peer is None or peer.revoked_at is not None:
        raise not_found("Tunnel peer")
    peer.revoked_at = utcnow()
    audit.record(
        db,
        request,
        "tunnel.peer_revoke",
        actor=user,
        entity_type="tunnel_peer",
        entity_id=peer.id,
        campus_id=peer.campus_id,
        details={"name": peer.name},
    )
    db.commit()
    tunnel.write_hub_conf(db)


@router.get("/hub-config")
def download_hub_config(request: Request, user: AdminUser, db: DbSession) -> Response:
    """The generated hub wg0.conf. Contains the hub private key and every peer's pre-shared key: admins only."""
    try:
        text = tunnel.hub_config(db)
    except tunnel.TunnelError as exc:
        raise ApiError(409, exc.code, exc.message) from exc
    audit.record(db, request, "tunnel.hub_config_download", actor=user, entity_type="tunnel_hub")
    db.commit()
    return Response(text, media_type="text/plain", headers={"Content-Disposition": 'attachment; filename="wg0.conf"'})
