"""Tunnel peer management and WireGuard config generation. Text and bookkeeping only: nothing here talks to a real
WireGuard hub (see docs/tunnel.md for what remains unverified)."""

import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditLog, TunnelPeer
from app.models.enums import PeerMode, Role
from app.services import tunnel
from tests.conftest import Api, make_campus, make_user


def test_x25519_matches_the_rfc7748_test_vector() -> None:
    """RFC 7748 section 6.1, Alice. WireGuard public keys are Curve25519 of the private key."""
    priv = base64.b64encode(bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")).decode()
    expected = base64.b64encode(
        bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
    ).decode()
    assert tunnel.public_from_private(priv) == expected


def test_generated_keypair_is_consistent_and_valid() -> None:
    private, public = tunnel.generate_keypair()
    assert len(base64.b64decode(private)) == 32 and len(base64.b64decode(public)) == 32
    assert tunnel.public_from_private(private) == public
    assert tunnel.generate_keypair()[0] != private


@pytest.mark.parametrize("bad", ["", "not base64!!", base64.b64encode(b"short").decode(), "A" * 43])
def test_public_key_validation_rejects_garbage(bad: str) -> None:
    with pytest.raises(tunnel.TunnelError):
        tunnel.validate_public_key(bad)


def test_parse_wg_dump_reads_handshakes_and_counters() -> None:
    dump = (
        "hubpriv\thubpub\t51820\toff\n"
        "PEERKEY1=\t(none)\t1.2.3.4:5\t10.77.0.2/32\t1760000000\t100\t200\t25\n"
        "PEERKEY2=\t(none)\t(none)\t10.77.0.3/32\t0\t0\t0\t25\nbroken line\n"
    )
    stats = tunnel.parse_wg_dump(dump)
    assert stats["PEERKEY1="] == tunnel.WgPeerStat(1760000000, 100, 200)
    assert stats["PEERKEY2="].handshake_epoch == 0 and "broken line" not in stats


def test_peer_status_distinguishes_unknown_never_connected_connected_and_stale() -> None:
    now = datetime.now(UTC)
    p = SimpleNamespace(revoked_at=None, health_checked_at=None, last_handshake_at=None)
    assert tunnel.peer_status(p, now) == "unknown"  # no health data: never claim offline or online
    p.health_checked_at = now
    assert tunnel.peer_status(p, now) == "never_connected"
    p.last_handshake_at = now - timedelta(seconds=90)
    assert tunnel.peer_status(p, now) == "connected"
    p.last_handshake_at = now - timedelta(minutes=10)
    assert tunnel.peer_status(p, now) == "stale"
    p.revoked_at = now
    assert tunnel.peer_status(p, now) == "revoked"


def create(api: Api, campus_id: str, **over) -> dict:
    payload = {"campus_id": campus_id, "name": "Head office", "mode": "connector", "lan_cidr": "192.168.15.0/24"}
    payload.update(over)
    r = api.post("/api/v1/tunnel/peers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_hub_endpoint_reports_public_key_and_never_the_private_key(admin_api: Api) -> None:
    hub = admin_api.get("/api/v1/tunnel/hub").json()
    assert (
        hub["configured"] is True and hub["endpoint"] == "vpn.test.example:51820" and hub["hub_address"] == "10.77.0.1"
    )
    assert hub["public_key"] == tunnel.public_from_private(get_settings().wg_hub_private_key)
    assert get_settings().wg_hub_private_key not in str(hub)


def test_connector_peer_gets_unique_addresses_and_a_one_time_config(admin_api: Api, db) -> None:
    cid = str(make_campus(db, "C1").id)
    out = create(admin_api, cid)
    peer = out["peer"]
    assert (peer["tunnel_ip"], peer["virtual_subnet"], peer["lan_cidr"]) == (
        "10.77.0.2",
        "10.77.1.0/24",
        "192.168.15.0/24",
    )
    assert "10.77.1.<last number" in peer["nvr_address_pattern"]
    assert out["private_key_shown_once"] is True and out["warnings"] == []

    cfg = out["config"]
    assert "PrivateKey = " in cfg and "Address = 10.77.0.2/32" in cfg
    assert "PersistentKeepalive = 25" in cfg and "Endpoint = vpn.test.example:51820" in cfg
    assert "AllowedIPs = 10.77.0.1/32" in cfg  # the connector only routes the hub, not the whole internet
    assert "-d 10.77.1.0/24 -j NETMAP --to 192.168.15.0/24" in cfg and "MASQUERADE" in cfg
    assert cfg.count("PostDown") == 2

    # the peer private key is shown once and stored nowhere
    private = next(ln.split(" = ")[1] for ln in cfg.splitlines() if ln.startswith("PrivateKey"))
    row = db.scalar(select(TunnelPeer))
    assert private not in " ".join(str(getattr(row, c.name)) for c in TunnelPeer.__table__.columns)
    assert tunnel.public_from_private(private) == row.public_key
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "tunnel.peer_create"))
    assert audit is not None and private not in str(audit.details)
    assert private not in str(admin_api.get("/api/v1/tunnel/peers").json())


def test_overlapping_campus_lans_are_fine_for_connectors(admin_api: Api, db) -> None:
    a, b = str(make_campus(db, "A").id), str(make_campus(db, "B").id)
    p1 = create(admin_api, a, name="A conn")["peer"]
    p2 = create(admin_api, b, name="B conn")["peer"]  # same 192.168.15.0/24 at both sites
    assert p1["lan_cidr"] == p2["lan_cidr"] and p1["virtual_subnet"] != p2["virtual_subnet"]
    assert p2["virtual_subnet"] == "10.77.2.0/24" and p2["tunnel_ip"] == "10.77.0.3"


def test_router_peers_cannot_overlap_and_use_the_real_lan(admin_api: Api, db) -> None:
    a, b = str(make_campus(db, "A").id), str(make_campus(db, "B").id)
    _, pub = tunnel.generate_keypair()
    r1 = create(admin_api, a, mode="router", public_key=pub)
    assert r1["private_key_shown_once"] is False  # the router generated its own key
    # ...but the pre-shared key the hub requires must still reach it, once, or the handshake can never succeed
    assert (
        r1["config_kind"] == "settings"
        and "Pre-shared key:" in r1["config"]
        and "Allowed IPs:           10.77.0.1/32" in r1["config"]
    )
    psk = next(
        ln.split(":", 1)[1].strip() for ln in r1["config"].splitlines() if ln.strip().startswith("Pre-shared key")
    )
    assert f"PresharedKey = {psk}" in admin_api.get("/api/v1/tunnel/hub-config").text  # hub and router agree
    assert "PrivateKey" not in r1["config"].replace(get_settings().wg_hub_private_key, "")
    assert r1["peer"]["virtual_subnet"] == "192.168.15.0/24"
    _, pub2 = tunnel.generate_keypair()
    clash = admin_api.post("/api/v1/tunnel/peers", json={"campus_id": b, "name": "B", "mode": "router",
                                                         "lan_cidr": "192.168.15.0/24", "public_key": pub2})  # fmt: skip
    assert clash.status_code == 422 and clash.json()["error"]["code"] == "lan_overlap"
    assert (
        create(admin_api, b, mode="router", lan_cidr="192.168.16.0/24", public_key=pub2)["peer"]["tunnel_ip"]
        == "10.77.0.3"
    )


@pytest.mark.parametrize("cidr", ["192.168.15.0/23", "192.168.15.5/24", "8.8.8.0/24", "not-a-cidr", "fd00::/64"])
def test_lan_range_must_be_a_private_slash_24(admin_api: Api, db, cidr: str) -> None:
    cid = str(make_campus(db, "C").id)
    r = admin_api.post("/api/v1/tunnel/peers", json={"campus_id": cid, "name": "x", "lan_cidr": cidr})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_lan"


def test_duplicate_public_key_and_unknown_campus_rejected(admin_api: Api, db) -> None:
    cid = str(make_campus(db, "C").id)
    _, pub = tunnel.generate_keypair()
    create(admin_api, cid, mode="router", public_key=pub)
    dup = admin_api.post("/api/v1/tunnel/peers", json={"campus_id": cid, "name": "again", "mode": "router",
                                                       "lan_cidr": "192.168.20.0/24", "public_key": pub})  # fmt: skip
    assert dup.status_code == 422 and dup.json()["error"]["code"] == "duplicate_key"
    ghost = admin_api.post(
        "/api/v1/tunnel/peers", json={"campus_id": str(uuid.uuid4()), "name": "x", "lan_cidr": "192.168.1.0/24"}
    )
    assert ghost.status_code == 422


def test_revoking_frees_the_slot_and_removes_the_peer_from_the_hub_config(admin_api: Api, db) -> None:
    cid = str(make_campus(db, "C").id)
    first = create(admin_api, cid, name="first")["peer"]
    create(admin_api, cid, name="second")
    hub = admin_api.get("/api/v1/tunnel/hub-config")
    assert hub.status_code == 200 and "attachment" in hub.headers["content-disposition"]
    assert first["public_key"] in hub.text and "AllowedIPs = 10.77.0.2/32, 10.77.1.0/24" in hub.text
    assert "PrivateKey = " + get_settings().wg_hub_private_key in hub.text  # the hub's own key, admin-only download

    assert admin_api.delete(f"/api/v1/tunnel/peers/{first['id']}").status_code == 204
    assert first["public_key"] not in admin_api.get("/api/v1/tunnel/hub-config").text
    assert [p["name"] for p in admin_api.get("/api/v1/tunnel/peers").json()] == ["second"]
    assert admin_api.delete(f"/api/v1/tunnel/peers/{first['id']}").status_code == 404
    assert create(admin_api, cid, name="reuses slot")["peer"]["tunnel_ip"] == "10.77.0.2"  # slot 1 is free again


def test_hub_config_writes_to_disk_atomically_when_a_path_is_set(admin_api: Api, db, tmp_path, monkeypatch) -> None:
    target = tmp_path / "wireguard" / "wg0.conf"
    patched = get_settings().model_copy(update={"wg_hub_conf_path": str(target)})
    monkeypatch.setattr(tunnel, "get_settings", lambda: patched)
    create(admin_api, str(make_campus(db, "C").id))
    text = target.read_text(encoding="utf-8")
    assert "[Interface]" in text and "[Peer]" in text and "ListenPort = 51820" in text
    assert list(target.parent.glob("tmp*")) == []  # no temp files left behind


def test_missing_endpoint_produces_a_visible_warning_and_placeholder(admin_api: Api, db, monkeypatch) -> None:
    patched = get_settings().model_copy(update={"wg_hub_endpoint": ""})
    monkeypatch.setattr(tunnel, "get_settings", lambda: patched)
    out = create(admin_api, str(make_campus(db, "C").id))
    assert any("WG_HUB_ENDPOINT" in w for w in out["warnings"])
    assert "REPLACE_WITH_HUB_HOST" in out["config"]


def test_hub_config_requires_a_hub_key(admin_api: Api, monkeypatch) -> None:
    patched = get_settings().model_copy(update={"wg_hub_private_key": ""})
    monkeypatch.setattr(tunnel, "get_settings", lambda: patched)
    r = admin_api.get("/api/v1/tunnel/hub-config")
    assert r.status_code == 409 and r.json()["error"]["code"] == "hub_not_configured"


def test_non_admins_cannot_see_or_change_tunnel_data(db) -> None:
    c = make_campus(db, "C")
    make_user(db, "op", Role.OPERATOR, [c.id])
    op = Api()
    op.login("op")
    assert op.get("/api/v1/tunnel/hub").status_code == 403
    assert op.get("/api/v1/tunnel/peers").status_code == 403
    assert op.get("/api/v1/tunnel/hub-config").status_code == 403
    assert (
        op.post(
            "/api/v1/tunnel/peers", json={"campus_id": str(c.id), "name": "x", "lan_cidr": "192.168.1.0/24"}
        ).status_code
        == 403
    )
    assert Api().get("/api/v1/tunnel/peers").status_code == 401


def test_health_refresh_is_a_noop_without_wg_and_never_invents_a_state(admin_api: Api, db, monkeypatch) -> None:
    create(admin_api, str(make_campus(db, "C").id))
    monkeypatch.setattr(tunnel.shutil, "which", lambda _: None)
    assert tunnel.refresh_health(db) == 0
    assert admin_api.get("/api/v1/tunnel/peers").json()[0]["status"] == "unknown"


def test_health_refresh_applies_handshakes_from_wg_dump(admin_api: Api, db, monkeypatch) -> None:
    peer = create(admin_api, str(make_campus(db, "C").id))["peer"]
    now = int(datetime.now(UTC).timestamp())
    dump = (
        f"hub\thubpub\t51820\toff\n{peer['public_key']}\t(none)\t1.2.3.4:9\t10.77.0.2/32\t{now - 30}\t5000\t7000\t25\n"
    )
    monkeypatch.setattr(tunnel.shutil, "which", lambda _: "/usr/bin/wg")
    monkeypatch.setattr(tunnel.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=dump))
    assert tunnel.refresh_health(db) == 1
    got = admin_api.get("/api/v1/tunnel/peers").json()[0]
    assert got["status"] == "connected" and got["rx_bytes"] == 5000 and got["tx_bytes"] == 7000
    assert PeerMode.CONNECTOR.value == peer["mode"]
