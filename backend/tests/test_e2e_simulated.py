"""Whole stack against the SIMULATED Dahua server: HTTP API -> Celery (eager) -> real Dahua adapter -> HTTP/RTSP."""

from collections.abc import Iterator

import pytest

from tests.conftest import Api
from tests.support.fake_dahua import FakeDahuaServer, SimCamera, SimNvr


@pytest.fixture
def sim() -> Iterator[tuple[SimNvr, FakeDahuaServer]]:
    nvr = SimNvr(
        capacity=4,
        cameras=[
            SimCamera("Lobby", "DH-IPC-A"),
            SimCamera("Gate", "DH-IPC-B", rtsp_ok=False),
            SimCamera("Off", enable=False),
        ],
    )
    server = FakeDahuaServer(nvr).start()
    yield nvr, server
    server.stop()


def register(api: Api, server: FakeDahuaServer, password: str = "secret") -> dict:
    cid = api.post("/api/v1/campuses", json={"name": "Sim campus"}).json()["id"]
    r = api.post("/api/v1/nvrs", json={
        "campus_id": cid, "name": "Sim NVR", "connection_type": "lan", "host": "127.0.0.1",
        "http_port": server.http_port, "rtsp_port": server.rtsp_port, "username": "admin", "password": password,
    })  # fmt: skip
    assert r.status_code == 201
    return r.json()


def test_register_discover_and_inventory_end_to_end(admin_api: Api, sim) -> None:
    _, server = sim
    n = register(admin_api, server)
    assert n["status"] == "connected" and n["model"] == "DHI-NVR-SIMULATED" and n["utc_offset_minutes"] == 300
    cams = {c["channel_number"]: c for c in admin_api.get(f"/api/v1/nvrs/{n['id']}/cameras").json()}
    assert sorted(cams) == [1, 2]  # the disabled slot and the trailing entry are not cameras
    assert cams[1]["name"] == "Lobby" and cams[1]["status"] == "online"
    assert cams[2]["name"] == "Gate" and cams[2]["status"] == "offline"  # configured, but the stream does not answer
    caps = {c["name"]: c["status"] for c in admin_api.get(f"/api/v1/nvrs/{n['id']}/capabilities").json()}
    assert caps["recording_search"] == "supported_tested" and caps["recording_download"] == "unknown"


def test_camera_comes_back_online_after_the_next_discovery(admin_api: Api, sim) -> None:
    nvr, server = sim
    n = register(admin_api, server)
    nvr.cameras[1].rtsp_ok = True
    assert admin_api.post(f"/api/v1/nvrs/{n['id']}/discover").status_code == 202
    assert {c["status"] for c in admin_api.get(f"/api/v1/nvrs/{n['id']}/cameras").json()} == {"online"}


def test_wrong_password_ends_as_auth_failed_after_a_single_attempt(admin_api: Api, sim) -> None:
    nvr, server = sim
    n = register(admin_api, server, password="definitely-wrong")
    assert n["status"] == "auth_failed"
    assert len([p for p in nvr.requests if "getDeviceType" in p]) == 2  # one digest handshake, no retry storm
    assert "definitely-wrong" not in str(n)


def test_nvr_going_offline_is_reported(admin_api: Api, sim) -> None:
    nvr, server = sim
    n = register(admin_api, server)
    server.stop()
    assert admin_api.post(f"/api/v1/nvrs/{n['id']}/test").status_code == 202
    got = admin_api.get(f"/api/v1/nvrs/{n['id']}").json()
    assert got["status"] == "offline" and got["last_success_at"] is not None  # we remember when it last worked
    assert nvr is not None
