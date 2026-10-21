"""WireGuard campus tunnels: peer records, address allocation and config generation.

Everything here is generated text plus bookkeeping. NOTHING in this module has been verified end to end against a real
WireGuard hub or campus device (see docs/tunnel.md). Key generation and the dump parser are unit tested.

Addressing (one /16, the hub is 10.77.0.1):
  peer N tunnel address      10.77.0.<N+1>/32
  connector mode, campus N   virtual LAN 10.77.<N>.0/24  <->  real LAN (/24) via 1:1 NETMAP on the connector, so
                             campuses that all use 192.168.1.0/24 do not collide. The app addresses the NVR at
                             10.77.<N>.<last octet of its real LAN IP>.
  router mode                the campus router holds the tunnel and its real LAN is routed directly, so the real LAN
                             ranges of router-mode campuses must not overlap.
"""

import base64
import ipaddress
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models.enums import PeerMode
from app.models.tunnel import TunnelPeer

log = logging.getLogger(__name__)

HUB_IP = "10.77.0.1"
NETWORK = "10.77.0.0/16"
LISTEN_PORT = 51820
MAX_SLOT = 253
HANDSHAKE_FRESH = timedelta(minutes=3)  # WireGuard re-handshakes about every 2 minutes while traffic flows


class TunnelError(Exception):
    def __init__(self, message: str, code: str = "tunnel_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# ------------------------------------------------------------------------------------------------------ keys
def generate_keypair() -> tuple[str, str]:
    key = X25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def public_from_private(private_b64: str) -> str:
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()


def generate_psk() -> str:
    import secrets

    return base64.b64encode(secrets.token_bytes(32)).decode()


def validate_public_key(value: str) -> str:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except Exception as exc:
        raise TunnelError("Public key is not valid base64", "invalid_public_key") from exc
    if len(raw) != 32:
        raise TunnelError("A WireGuard public key is 32 bytes (44 base64 characters)", "invalid_public_key")
    return value.strip()


def hub_public_key() -> str | None:
    priv = get_settings().wg_hub_private_key
    return public_from_private(priv) if priv else None


# ------------------------------------------------------------------------------------------------------ peers
@dataclass(frozen=True)
class NewPeer:
    peer: TunnelPeer
    private_key: str | None  # only set when the server generated the keypair; shown once, never stored
    warnings: list[str]
    preshared_key: str  # shown once with the peer settings; the server keeps only an encrypted copy
    nvr_host: str | None = None  # windows mode: the NVR's real LAN address, used only to build the forwarding script


def active_peers(db: Session) -> list[TunnelPeer]:
    return list(db.scalars(select(TunnelPeer).where(TunnelPeer.revoked_at.is_(None)).order_by(TunnelPeer.slot)))


def parse_lan(cidr: str) -> ipaddress.IPv4Network:
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=True)
    except ValueError as exc:
        raise TunnelError("LAN range must be a network like 192.168.15.0/24", "invalid_lan") from exc
    if not isinstance(net, ipaddress.IPv4Network) or net.prefixlen != 24 or not net.is_private:
        raise TunnelError("Only private IPv4 /24 campus LANs are supported in this version", "invalid_lan")
    return net


def route_cidr(peer: TunnelPeer) -> str | None:
    """Extra range the hub routes to this peer besides its own tunnel address: the virtual range for connectors, the
    real LAN for routers, nothing for Windows (the PC forwards ports from its tunnel address instead)."""
    if peer.mode == PeerMode.WINDOWS:
        return None
    return peer.lan_cidr if peer.mode == PeerMode.ROUTER else peer.virtual_subnet


def nvr_address_pattern(peer: TunnelPeer) -> str:
    if peer.mode == PeerMode.WINDOWS:
        return f"the tunnel address {peer.tunnel_ip} (the Windows PC forwards the NVR's ports)"
    if peer.mode == PeerMode.ROUTER:
        return "the NVR's real LAN address (for example " + peer.lan_cidr.rsplit(".", 1)[0] + ".6)"
    return peer.virtual_subnet.rsplit(".", 1)[0] + ".<last number of the NVR's real LAN address>"


def parse_nvr_host(value: str | None, lan: ipaddress.IPv4Network | None) -> ipaddress.IPv4Address:
    if not value:
        raise TunnelError("Enter the NVR's real LAN address for the Windows forwarding script", "invalid_nvr_host")
    try:
        ip = ipaddress.IPv4Address(value.strip())
    except ValueError as exc:
        raise TunnelError("The NVR address must be an IPv4 address like 192.168.15.6", "invalid_nvr_host") from exc
    if not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise TunnelError("The NVR address must be a private LAN address", "invalid_nvr_host")
    if lan is not None and ip not in lan:
        raise TunnelError(f"The NVR address {ip} is not inside the campus LAN {lan}", "invalid_nvr_host")
    return ip


def create_peer(
    db: Session,
    campus_id: object,
    name: str,
    mode: PeerMode,
    lan_cidr: str | None,
    public_key: str | None,
    nvr_host: str | None = None,
) -> NewPeer:
    host_ip: ipaddress.IPv4Address | None = None
    if mode == PeerMode.WINDOWS:
        # The LAN range is informational here: use the one given, or the /24 around the NVR's address.
        lan_given = parse_lan(lan_cidr) if lan_cidr else None
        host_ip = parse_nvr_host(nvr_host, lan_given)
        lan = lan_given or ipaddress.ip_network(f"{host_ip}/24", strict=False)
    else:
        if not lan_cidr:
            raise TunnelError("LAN range must be a network like 192.168.15.0/24", "invalid_lan")
        lan = parse_lan(lan_cidr)
    used = {p.slot for p in active_peers(db)}
    slot = next((s for s in range(1, MAX_SLOT + 1) if s not in used), None)
    if slot is None:
        raise TunnelError("No free tunnel addresses left", "no_free_slot")
    if mode == PeerMode.ROUTER:
        for other in active_peers(db):
            if other.mode == PeerMode.ROUTER and ipaddress.ip_network(other.lan_cidr).overlaps(lan):
                raise TunnelError(
                    f"LAN range {lan} overlaps router-mode campus peer '{other.name}'. Use a connector instead.",
                    "lan_overlap",
                )

    private: str | None = None
    if public_key:
        public = validate_public_key(public_key)
    else:
        private, public = generate_keypair()
    if any(p.public_key == public for p in active_peers(db)):
        raise TunnelError("A peer with this public key already exists", "duplicate_key")

    psk = generate_psk()
    peer = TunnelPeer(
        campus_id=campus_id,
        name=name,
        mode=mode,
        public_key=public,
        preshared_key_encrypted=encrypt_secret(psk),
        slot=slot,
        tunnel_ip=f"10.77.0.{slot + 1}",
        virtual_subnet=(
            f"10.77.{slot}.0/24"
            if mode == PeerMode.CONNECTOR
            else f"10.77.0.{slot + 1}/32"
            if mode == PeerMode.WINDOWS
            else str(lan)
        ),
        lan_cidr=str(lan),
    )
    db.add(peer)
    db.flush()

    warnings: list[str] = []
    if not get_settings().wg_hub_endpoint:
        warnings.append("WG_HUB_ENDPOINT is not set, so the generated config has a placeholder hub address")
    if not get_settings().wg_hub_private_key:
        warnings.append("WG_HUB_PRIVATE_KEY is not set (run scripts/init-env.py); the hub config cannot be generated")
    return NewPeer(
        peer=peer, private_key=private, warnings=warnings, preshared_key=psk, nvr_host=str(host_ip) if host_ip else None
    )


# ------------------------------------------------------------------------------------------------------ configs
def hub_config(db: Session) -> str:
    s = get_settings()
    if not s.wg_hub_private_key:
        raise TunnelError("WG_HUB_PRIVATE_KEY is not configured", "hub_not_configured")
    lines = [
        "# NVR Central WireGuard hub. Generated, do not edit by hand.",
        "[Interface]",
        f"Address = {HUB_IP}/16",
        f"ListenPort = {LISTEN_PORT}",
        f"PrivateKey = {s.wg_hub_private_key}",
    ]
    for p in active_peers(db):
        extra = route_cidr(p)
        lines += [
            "",
            f"# {p.name} ({p.mode.value}, slot {p.slot})",
            "[Peer]",
            f"PublicKey = {p.public_key}",
            f"PresharedKey = {decrypt_secret(p.preshared_key_encrypted)}",
            f"AllowedIPs = {p.tunnel_ip}/32" + (f", {extra}" if extra else ""),
        ]
    return "\n".join(lines) + "\n"


def peer_config(peer: TunnelPeer, private_key: str) -> str:
    s = get_settings()
    hub_pub = hub_public_key() or "HUB_PUBLIC_KEY_NOT_CONFIGURED"
    endpoint = s.wg_hub_endpoint or f"REPLACE_WITH_HUB_HOST:{LISTEN_PORT}"
    iface = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {peer.tunnel_ip}/32",
    ]
    if peer.mode == PeerMode.CONNECTOR:
        v, lan = peer.virtual_subnet, peer.lan_cidr
        iface += [
            "# Connector (Linux). Maps the virtual range back onto the real campus LAN, 1:1, and masquerades so the",
            "# NVR sees the connector as an ordinary LAN client. Change eth0 to the connector's LAN interface.",
            "PostUp = sysctl -w net.ipv4.ip_forward=1",
            f"PostUp = iptables -t nat -A PREROUTING -i %i -d {v} -j NETMAP --to {lan}",
            f"PostUp = iptables -t nat -A POSTROUTING -o eth0 -d {lan} -j MASQUERADE",
            f"PostDown = iptables -t nat -D PREROUTING -i %i -d {v} -j NETMAP --to {lan}",
            f"PostDown = iptables -t nat -D POSTROUTING -o eth0 -d {lan} -j MASQUERADE",
        ]
    peer_section = [
        "",
        "[Peer]",
        f"PublicKey = {hub_pub}",
        f"PresharedKey = {decrypt_secret(peer.preshared_key_encrypted)}",
        f"Endpoint = {endpoint}",
        f"AllowedIPs = {HUB_IP}/32",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(iface + peer_section) + "\n"


def peer_settings(peer: TunnelPeer, preshared_key: str) -> str:
    """Settings sheet for a peer that made its own keypair (a router, or a connector that generated its own key).
    Without the pre-shared key the hub would reject the handshake, so it must be shown once at creation."""
    s = get_settings()
    hub_pub = hub_public_key() or "HUB_PUBLIC_KEY_NOT_CONFIGURED"
    endpoint = s.wg_hub_endpoint or f"REPLACE_WITH_HUB_HOST:{LISTEN_PORT}"
    return "\n".join(
        [
            f'WireGuard settings for "{peer.name}". Enter these on the campus router or device.',
            "",
            "Interface (the campus side)",
            "  Private key:           the one your router generated (we only hold its public key)",
            f"  Address:               {peer.tunnel_ip}/32",
            "",
            "Peer (this server, the hub)",
            f"  Public key:            {hub_pub}",
            f"  Pre-shared key:        {preshared_key}",
            f"  Endpoint:              {endpoint}",
            f"  Allowed IPs:           {HUB_IP}/32",
            "  Persistent keepalive:  25 seconds",
            "",
            *(
                [
                    "Forwarding",
                    "  Run the PowerShell script (shown with these settings) as Administrator on the Windows PC.",
                    "  It forwards the NVR's ports from the tunnel address to the NVR.",
                ]
                if peer.mode == PeerMode.WINDOWS
                else [
                    "Routing and firewall",
                    f"  Route {HUB_IP}/32 through the tunnel. Do NOT send all traffic through it.",
                    f"  Allow traffic from {HUB_IP} into the campus LAN {peer.lan_cidr}.",
                ]
            ),
            "",
        ]
    )


def windows_script(peer: TunnelPeer, nvr_host: str, ports: list[int]) -> str:
    """PowerShell for the Windows connector: forwards the NVR's ports from the tunnel address (netsh portproxy) and
    allows only the hub to use them. Inputs are an IPv4 address and integers that this module validated, so nothing
    user-typed reaches the script unchecked. UNVERIFIED on real Windows hardware."""
    port_list = ",".join(str(p) for p in ports)
    return "\n".join(
        [
            f"# NVR Central: Windows connector for '{peer.name}'. Run in PowerShell as Administrator.",
            "# Safe to run again. Do this AFTER the WireGuard tunnel is active (the tunnel address must exist).",
            "$ErrorActionPreference = 'Stop'",
            f"$tunnel = '{peer.tunnel_ip}'",
            f"$nvr = '{nvr_host}'",
            f"$hub = '{HUB_IP}'",
            f"$ports = {port_list}",
            "",
            "Set-Service iphlpsvc -StartupType Automatic",
            "Start-Service iphlpsvc",
            "foreach ($p in $ports) {",
            "  netsh interface portproxy delete v4tov4 listenaddress=$tunnel listenport=$p 2>$null | Out-Null",
            "  netsh interface portproxy add v4tov4 listenaddress=$tunnel listenport=$p "
            "connectaddress=$nvr connectport=$p",
            "}",
            "",
            "Remove-NetFirewallRule -DisplayName 'NVR Central forward' -ErrorAction SilentlyContinue",
            "New-NetFirewallRule -DisplayName 'NVR Central forward' -Direction Inbound -Protocol TCP "
            "-LocalPort $ports -RemoteAddress $hub -Action Allow | Out-Null",
            "",
            "# The forwarder binds to the tunnel address, which only exists once WireGuard is up. Restart it shortly",
            "# after every boot so it binds even when the tunnel service started later.",
            "schtasks /Create /TN 'NVR Central restart IP Helper' /SC ONSTART /DELAY 0001:00 /RU SYSTEM "
            "/TR 'powershell -NoProfile -Command Restart-Service iphlpsvc' /F | Out-Null",
            "",
            "netsh interface portproxy show v4tov4",
            "Write-Host \"Done. Register the NVR in NVR Central at $tunnel with ports $($ports -join ', ').\"",
            "",
        ]
    )


def write_hub_conf(db: Session) -> bool:
    """Write the hub config to WG_HUB_CONF_PATH if set. The hub must be told to reload it (see docs/tunnel.md)."""
    path = get_settings().wg_hub_conf_path
    if not path or not get_settings().wg_hub_private_key:
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=target.parent, delete=False, encoding="utf-8") as tmp:
        tmp.write(hub_config(db))
    Path(tmp.name).replace(target)
    return True


# ------------------------------------------------------------------------------------------------------ health
@dataclass(frozen=True)
class WgPeerStat:
    handshake_epoch: int
    rx: int
    tx: int


def parse_wg_dump(text: str) -> dict[str, WgPeerStat]:
    """`wg show <if> dump`: the first line is the interface, then one tab-separated line per peer:
    public-key, preshared-key, endpoint, allowed-ips, latest-handshake, rx, tx, keepalive."""
    stats: dict[str, WgPeerStat] = {}
    for line in text.strip().splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 8:
            continue
        try:
            stats[cols[0]] = WgPeerStat(int(cols[4]), int(cols[5]), int(cols[6]))
        except ValueError:
            continue
    return stats


def refresh_health(db: Session) -> int:
    """Read handshake times from the local `wg` tool. Returns peers updated, 0 when wg is unavailable (peers then
    stay 'unknown': we never invent a health state)."""
    if shutil.which("wg") is None:
        return 0
    try:
        out = subprocess.run(
            ["wg", "show", get_settings().wg_interface, "dump"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (subprocess.SubprocessError, OSError):
        log.warning("wg show failed; tunnel health not updated")
        return 0
    stats = parse_wg_dump(out)
    now = datetime.now(UTC)
    updated = 0
    for peer in active_peers(db):
        st = stats.get(peer.public_key)
        if st is None:
            continue
        peer.last_handshake_at = datetime.fromtimestamp(st.handshake_epoch, UTC) if st.handshake_epoch > 0 else None
        peer.rx_bytes, peer.tx_bytes, peer.health_checked_at = st.rx, st.tx, now
        updated += 1
    db.commit()
    return updated


def peer_status(peer: TunnelPeer, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if peer.revoked_at is not None:
        return "revoked"
    if peer.health_checked_at is None:
        return "unknown"
    if peer.last_handshake_at is None:
        return "never_connected"
    return "connected" if now - peer.last_handshake_at <= HANDSHAKE_FRESH else "stale"
