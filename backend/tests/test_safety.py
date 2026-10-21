"""Guards that keep the test suite (and CI) from ever sending logins to real devices."""

import socket
from typing import Any

from app.integrations.dahua import cgi
from app.models.enums import NvrStatus
from tests.conftest import Api


def test_registering_an_nvr_on_a_real_lan_address_never_touches_the_network(admin_api: Api, monkeypatch: Any) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(cgi.DahuaHttp, "__init__", forbidden)
    cid = admin_api.post("/api/v1/campuses", json={"name": "Safety"}).json()["id"]
    r = admin_api.post(
        "/api/v1/nvrs",
        json={"campus_id": cid, "name": "Real-looking NVR", "connection_type": "lan", "host": "192.168.15.6",
              "username": "admin", "password": "wrong-on-purpose"},
    )  # fmt: skip
    assert r.status_code == 201
    assert r.json()["status"] == NvrStatus.OFFLINE.value and "blocked in tests" in r.json()["status_message"]
