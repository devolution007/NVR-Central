"""Windows connector mode: WireGuard for Windows plus netsh port forwarding. Generated text only, UNVERIFIED on a real
Windows PC (see docs/tunnel.md)."""

import re

import pytest

from app.core.config import get_settings
from app.services import tunnel
from tests.conftest import Api, make_campus


def create(api: Api, campus_id: str, **over) -> dict:
    payload = {"campus_id": campus_id, "name": "Reception PC", "mode": "windows", "nvr_host": "192.168.15.6"}
    payload.update(over)
    r = api.post("/api/v1/tunnel/peers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_windows_peer_gets_a_plain_wireguard_config_without_linux_nat_rules(admin_api: Api, db) -> None:
    out = create(admin_api, str(make_campus(db, "C").id))
    peer = out["peer"]
    assert peer["mode"] == "windows" and peer["tunnel_ip"] == "10.77.0.2"
    assert peer["lan_cidr"] == "192.168.15.0/24"  # derived from the NVR address when not given
    assert "10.77.0.2" in peer["nvr_address_pattern"] and "forwards" in peer["nvr_address_pattern"]

    cfg = out["config"]
    assert out["config_kind"] == "wg_quick" and "PrivateKey = " in cfg and "Address = 10.77.0.2/32" in cfg
    assert "AllowedIPs = 10.77.0.1/32" in cfg and "PersistentKeepalive = 25" in cfg
    # none of the Linux-only machinery may leak into a config that WireGuard for Windows will import
    for linux_only in ("iptables", "NETMAP", "sysctl", "PostUp", "PostDown", "eth0"):
        assert linux_only not in cfg


def test_forwarding_script_targets_the_nvr_and_only_admits_the_hub(admin_api: Api, db) -> None:
    out = create(admin_api, str(make_campus(db, "C").id), nvr_http_port=8080, nvr_rtsp_port=8554)
    s = out["script"]
    assert out["script_name"] == "nvr-central-forward.ps1"
    assert "$tunnel = '10.77.0.2'" in s and "$nvr = '192.168.15.6'" in s and "$hub = '10.77.0.1'" in s
    assert "$ports = 8080,8554" in s
    assert "netsh interface portproxy add v4tov4 listenaddress=$tunnel" in s
    assert "-RemoteAddress $hub" in s and "-Direction Inbound" in s  # nothing else on the LAN can use the forward
    assert "iphlpsvc" in s and "schtasks" in s  # IP Helper must run, and be restarted after boot
    assert "Run in PowerShell as Administrator" in s


def test_script_is_rebuilt_only_from_validated_values(admin_api: Api, db) -> None:
    cid = str(make_campus(db, "C").id)
    for bad in (
        "192.168.15.6'; Remove-Item C:\\ -Recurse #",
        "evil.example.com",
        "8.8.8.8",
        "127.0.0.1",
        "169.254.1.1",
        "",
    ):
        r = admin_api.post(
            "/api/v1/tunnel/peers", json={"campus_id": cid, "name": "x", "mode": "windows", "nvr_host": bad}
        )
        assert r.status_code == 422, bad
        assert r.json()["error"]["code"] == "invalid_nvr_host" or r.json()["error"]["code"] == "validation_error"


def test_nvr_host_is_required_and_must_sit_inside_a_given_lan(admin_api: Api, db) -> None:
    cid = str(make_campus(db, "C").id)
    missing = admin_api.post("/api/v1/tunnel/peers", json={"campus_id": cid, "name": "x", "mode": "windows"})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "invalid_nvr_host"
    outside = admin_api.post("/api/v1/tunnel/peers", json={"campus_id": cid, "name": "x", "mode": "windows",
                                                           "nvr_host": "192.168.99.6", "lan_cidr": "192.168.15.0/24"})  # fmt: skip
    assert outside.status_code == 422 and "not inside" in outside.json()["error"]["message"]


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_forwarded_ports_must_be_valid(admin_api: Api, db, port: int) -> None:
    cid = str(make_campus(db, "C").id)
    r = admin_api.post("/api/v1/tunnel/peers", json={"campus_id": cid, "name": "x", "mode": "windows",
                                                     "nvr_host": "192.168.15.6", "nvr_http_port": port})  # fmt: skip
    assert r.status_code == 422


def test_hub_routes_only_the_windows_pcs_tunnel_address_and_overlapping_lans_are_fine(admin_api: Api, db) -> None:
    a, b = str(make_campus(db, "A").id), str(make_campus(db, "B").id)
    create(admin_api, a, name="A pc")
    create(admin_api, b, name="B pc")  # the same 192.168.15.x range at both campuses is not a problem here
    hub = admin_api.get("/api/v1/tunnel/hub-config").text
    assert "AllowedIPs = 10.77.0.2/32\n" in hub and "AllowedIPs = 10.77.0.3/32\n" in hub
    assert "10.77.1.0/24" not in hub and "192.168.15.0/24" not in hub  # no virtual or LAN routes for Windows peers


def test_supplied_public_key_gets_the_settings_sheet_and_the_script(admin_api: Api, db) -> None:
    _, pub = tunnel.generate_keypair()
    out = create(admin_api, str(make_campus(db, "C").id), public_key=pub)
    assert out["config_kind"] == "settings" and out["private_key_shown_once"] is False
    assert "Pre-shared key:" in out["config"] and "Run the PowerShell script" in out["config"]
    assert out["script"] is not None
    psk = re.search(r"Pre-shared key:\s+(\S+)", out["config"]).group(1)
    assert f"PresharedKey = {psk}" in admin_api.get("/api/v1/tunnel/hub-config").text


def test_other_modes_do_not_produce_a_script(admin_api: Api, db) -> None:
    cid = str(make_campus(db, "C").id)
    r = admin_api.post(
        "/api/v1/tunnel/peers", json={"campus_id": cid, "name": "x", "mode": "connector", "lan_cidr": "192.168.15.0/24"}
    )
    assert r.status_code == 201 and r.json()["script"] is None and "NETMAP" in r.json()["config"]


def test_windows_peer_private_key_is_not_stored_or_listed(admin_api: Api, db) -> None:
    out = create(admin_api, str(make_campus(db, "C").id))
    private = next(ln.split(" = ")[1] for ln in out["config"].splitlines() if ln.startswith("PrivateKey"))
    assert private not in str(admin_api.get("/api/v1/tunnel/peers").json())
    assert private not in admin_api.get("/api/v1/tunnel/hub-config").text
    assert tunnel.public_from_private(private) == out["peer"]["public_key"]
    assert get_settings().wg_hub_private_key != private
