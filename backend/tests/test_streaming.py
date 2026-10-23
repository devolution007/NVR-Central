"""Live viewing: sessions, limits, authorisation, MediaMTX configuration and the HLS proxy.
MediaMTX is SIMULATED (tests/support/fake_mediamtx.py); real streaming is covered by the hardware run in docs."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import encrypt_secret
from app.db.base import utcnow
from app.models import Camera, CameraStreamProfile, Nvr, StreamSession
from app.models.enums import (
    CameraStatus,
    ConnectionType,
    NvrStatus,
    Role,
    StreamKind,
    StreamSessionStatus,
)
from app.streaming import manager, mediamtx
from tests.conftest import Api, make_campus, make_user
from tests.support.fake_mediamtx import FakeMediaMtx

PW = "p@ss:w/rd#1"  # special characters must be URL-encoded in the source URL


@dataclass
class World:
    campus_id: uuid.UUID
    nvr: Nvr
    cams: list[Camera]


def make_world(db: Any, *, cameras: int = 3, codec: str = "H.265", campus_name: str = "Live campus") -> World:
    campus = make_campus(db, campus_name)
    nvr = Nvr(
        campus_id=campus.id, name=f"NVR {campus_name}", connection_type=ConnectionType.LAN, host="10.0.0.5",
        http_port=80, rtsp_port=554, username="viewer user", password_encrypted=encrypt_secret(PW),
        status=NvrStatus.CONNECTED, last_success_at=utcnow(),
    )  # fmt: skip
    db.add(nvr)
    db.flush()
    cams = []
    for i in range(1, cameras + 1):
        cam = Camera(nvr_id=nvr.id, campus_id=campus.id, channel_number=i, name=f"Cam {i}", status=CameraStatus.ONLINE)
        db.add(cam)
        db.flush()
        for kind in StreamKind:
            db.add(CameraStreamProfile(camera_id=cam.id, stream=kind, codec=codec, width=704, height=576))
        cams.append(cam)
    db.commit()
    return World(campus.id, nvr, cams)


def start(api: Api, cam: Camera, stream: str = "sub") -> Any:
    return api.post(f"/api/v1/cameras/{cam.id}/stream-session", json={"stream": stream})


@pytest.fixture
def world(db: Any) -> World:
    return make_world(db)


def test_substream_of_an_h265_camera_is_pulled_then_converted_without_leaking_credentials(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx
) -> None:
    r = start(admin_api, world.cams[0])
    assert r.status_code == 201
    out = r.json()
    assert out["stream"] == "sub" and out["transcoded"] is True
    assert out["hls_url"] == f"/api/v1/stream-sessions/{out['id']}/hls/index.m3u8"
    assert out["keepalive_every_seconds"] == 20

    cam = world.cams[0]
    raw, live = f"raw-{cam.id.hex}-sub", f"live-{cam.id.hex}-sub"
    assert set(mediamtx.paths) == {raw, live}
    src = mediamtx.paths[raw]
    assert src["source"] == "rtsp://viewer%20user:p%40ss%3Aw%2Frd%231@10.0.0.5:554/cam/realmonitor?channel=1&subtype=1"
    assert src["sourceOnDemand"] is True and src["rtspTransport"] == "tcp"  # only pulled while watched, over TCP
    conv = mediamtx.paths[live]
    assert f"-i rtsp://127.0.0.1:$RTSP_PORT/{raw}" in conv["runOnDemand"] and "libx264" in conv["runOnDemand"]
    assert (
        "p%40ss" not in conv["runOnDemand"] and "10.0.0.5" not in conv["runOnDemand"]
    )  # ffmpeg never sees credentials
    for secret in (PW, "p%40ss", "viewer"):
        assert secret not in r.text


def test_h264_camera_is_served_directly_without_conversion(db: Any, admin_api: Api, mediamtx: FakeMediaMtx) -> None:
    w = make_world(db, codec="H.264", campus_name="h264")
    out = start(admin_api, w.cams[0]).json()
    assert out["transcoded"] is False
    assert list(mediamtx.paths) == [f"live-{w.cams[0].id.hex}-sub"]
    assert "source" in mediamtx.paths[f"live-{w.cams[0].id.hex}-sub"]


def test_main_stream_uses_subtype_zero_and_the_cameras_channel(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx
) -> None:
    start(admin_api, world.cams[1], "main")
    src = mediamtx.paths[f"raw-{world.cams[1].id.hex}-main"]["source"]
    assert src.endswith("channel=2&subtype=0")


@pytest.mark.parametrize(
    ("mode", "codec", "expected"), [("always", "H.264", True), ("never", "H.265", False), ("auto", None, True)]
)
def test_transcode_mode_setting(
    admin_api: Api, db: Any, monkeypatch, mode: str, codec: str | None, expected: bool
) -> None:
    w = make_world(db, campus_name=f"m-{mode}")
    if codec is None:
        db.query(CameraStreamProfile).delete()
        db.commit()
    else:
        for p in db.scalars(select(CameraStreamProfile)):
            p.codec = codec
        db.commit()
    patched = get_settings().model_copy(update={"stream_transcode": mode})
    monkeypatch.setattr(manager, "get_settings", lambda: patched)
    assert start(admin_api, w.cams[0]).json()["transcoded"] is expected


def test_a_second_viewer_shares_the_stream_and_costs_the_nvr_nothing(
    admin_api: Api, world: World, db: Any, mediamtx: FakeMediaMtx
) -> None:
    make_user(db, "vw", Role.VIEWER, [world.campus_id])
    other = Api()
    other.login("vw")
    a, b = start(admin_api, world.cams[0]), start(other, world.cams[0])
    assert a.status_code == b.status_code == 201 and a.json()["id"] != b.json()["id"]
    assert manager.active_sessions_for_nvr(db, world.nvr.id) == 1


def test_per_nvr_limit_counts_distinct_streams_and_frees_on_stop(admin_api: Api, db: Any, monkeypatch) -> None:
    w = make_world(db, cameras=5, campus_name="limit")
    patched = get_settings().model_copy(update={"max_streams_per_nvr": 3})
    monkeypatch.setattr(manager, "get_settings", lambda: patched)
    ids = [start(admin_api, c).json()["id"] for c in w.cams[:3]]
    blocked = start(admin_api, w.cams[3])
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "nvr_stream_limit"
    assert "3 live streams" in blocked.json()["error"]["message"]
    # the main stream of an already-watched camera is a different upstream session, so it counts too
    assert start(admin_api, w.cams[0], "main").status_code == 429
    # re-joining a stream already live is allowed even at the limit
    assert start(admin_api, w.cams[1]).status_code == 201
    assert admin_api.delete(f"/api/v1/stream-sessions/{ids[0]}").status_code == 204
    assert admin_api.delete(f"/api/v1/stream-sessions/{ids[1]}").status_code == 204
    still_live = db.scalars(
        select(StreamSession).where(
            StreamSession.camera_id == w.cams[1].id, StreamSession.status == StreamSessionStatus.ACTIVE
        )
    ).all()
    assert len(still_live) == 1  # the re-joined viewer is still watching camera 2
    assert start(admin_api, w.cams[3]).status_code == 201  # camera 1 freed a slot


def test_an_nvrs_own_limit_overrides_the_server_default(admin_api: Api, db: Any) -> None:
    w = make_world(db, cameras=11, campus_name="own limit")
    for c in w.cams[:8]:
        assert start(admin_api, c).status_code == 201
    assert start(admin_api, w.cams[8]).status_code == 429  # server default of 8
    r = admin_api.get(f"/api/v1/nvrs/{w.nvr.id}").json()
    assert r["max_live_streams"] is None and r["effective_max_live_streams"] == 8

    body = {
        "campus_id": str(w.campus_id), "name": w.nvr.name, "connection_type": "lan", "host": "10.0.0.5",
        "http_port": 80, "rtsp_port": 554, "username": "viewer user", "enabled": True, "max_live_streams": 10,
    }  # fmt: skip
    put = admin_api.put(f"/api/v1/nvrs/{w.nvr.id}", json=body)
    assert (
        put.status_code == 200
        and put.json()["max_live_streams"] == 10
        and put.json()["effective_max_live_streams"] == 10
    )
    assert put.json()["status"] == "connected"  # changing the limit does not reset the connection status
    assert start(admin_api, w.cams[8]).status_code == 201 and start(admin_api, w.cams[9]).status_code == 201
    blocked = start(admin_api, w.cams[10])
    assert blocked.status_code == 429 and "10 live streams" in blocked.json()["error"]["message"]

    assert admin_api.put(f"/api/v1/nvrs/{w.nvr.id}", json={**body, "max_live_streams": 0}).status_code == 422
    cleared = admin_api.put(f"/api/v1/nvrs/{w.nvr.id}", json={**body, "max_live_streams": None}).json()
    assert cleared["max_live_streams"] is None and cleared["effective_max_live_streams"] == 8


def test_expired_sessions_stop_counting_toward_the_limit(admin_api: Api, db: Any, monkeypatch) -> None:
    w = make_world(db, cameras=3, campus_name="expiry")
    patched = get_settings().model_copy(update={"max_streams_per_nvr": 1})
    monkeypatch.setattr(manager, "get_settings", lambda: patched)
    first = start(admin_api, w.cams[0]).json()["id"]
    assert start(admin_api, w.cams[1]).status_code == 429
    sess = db.get(StreamSession, uuid.UUID(first))
    sess.expires_at = utcnow() - timedelta(seconds=1)  # the browser vanished
    db.commit()
    assert start(admin_api, w.cams[1]).status_code == 201
    assert manager.sweep_expired(db) == 1
    db.expire_all()
    assert db.get(StreamSession, uuid.UUID(first)).status == StreamSessionStatus.EXPIRED


def test_total_limit_across_the_server(admin_api: Api, db: Any, monkeypatch) -> None:
    w = make_world(db, cameras=3, campus_name="total")
    patched = get_settings().model_copy(update={"max_streams_total": 2})
    monkeypatch.setattr(manager, "get_settings", lambda: patched)
    assert start(admin_api, w.cams[0]).status_code == 201 and start(admin_api, w.cams[1]).status_code == 201
    r = start(admin_api, w.cams[2])
    assert r.status_code == 429 and r.json()["error"]["code"] == "stream_limit"


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"connection_type": ConnectionType.P2P, "host": None}, "P2P"),
        ({"enabled": False}, "disabled"),
        ({"status": NvrStatus.AUTH_FAILED}, "rejected its credentials"),
        ({"last_success_at": None}, "never connected"),
    ],
)
def test_nvrs_that_must_not_be_streamed_are_refused_with_a_reason(
    admin_api: Api, db: Any, mediamtx: FakeMediaMtx, change: dict, fragment: str
) -> None:
    w = make_world(db, campus_name=f"blocked-{fragment[:4]}")
    for k, v in change.items():
        setattr(w.nvr, k, v)
    db.commit()
    r = start(admin_api, w.cams[0])
    assert (
        r.status_code == 409
        and r.json()["error"]["code"] == "stream_not_available"
        and fragment in r.json()["error"]["message"]
    )
    assert mediamtx.paths == {}  # nothing was sent to the gateway, so the NVR was never contacted
    info = admin_api.get(f"/api/v1/cameras/{w.cams[0].id}/live").json()
    assert info["can_stream"] is False and fragment in info["reason"]


def test_removed_camera_cannot_be_streamed(admin_api: Api, world: World, db: Any) -> None:
    world.cams[0].status = CameraStatus.UNAVAILABLE
    db.commit()
    assert start(admin_api, world.cams[0]).status_code == 409


def test_live_info_reports_codec_conversion_and_room_left(admin_api: Api, world: World) -> None:
    start(admin_api, world.cams[0])
    info = admin_api.get(f"/api/v1/cameras/{world.cams[1].id}/live").json()
    assert info == {"can_stream": True, "reason": None, "stream": "sub", "codec": "H.265", "transcode": True,
                    "nvr_active_streams": 1, "nvr_limit": 8}  # fmt: skip


def test_authorisation_on_every_entry_point(world: World, db: Any, admin_api: Api) -> None:
    other = make_campus(db, "Other campus")
    make_user(db, "outsider", Role.VIEWER, [other.id])
    outsider = Api()
    outsider.login("outsider")
    cam = world.cams[0]
    assert start(outsider, cam).status_code == 404  # looks the same as a camera that does not exist
    assert outsider.get(f"/api/v1/cameras/{cam.id}/live").status_code == 404
    assert Api().client.post(f"/api/v1/cameras/{cam.id}/stream-session", json={}).status_code == 401
    # cookie auth needs the CSRF header like every other write
    no_csrf = admin_api.client.post(f"/api/v1/cameras/{cam.id}/stream-session", json={})
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "csrf_failed"


def test_all_roles_with_campus_access_can_watch(world: World, db: Any) -> None:
    for role in (Role.OPERATOR, Role.VIEWER):
        make_user(db, role.value, role, [world.campus_id])
        a = Api()
        a.login(role.value)
        assert start(a, world.cams[0]).status_code == 201


def test_sessions_belong_to_their_owner(admin_api: Api, world: World, db: Any) -> None:
    make_user(db, "vw", Role.VIEWER, [world.campus_id])
    viewer = Api()
    viewer.login("vw")
    sid = start(viewer, world.cams[0]).json()["id"]
    assert (
        admin_api.post(f"/api/v1/stream-sessions/{sid}/keepalive").status_code == 404
    )  # not their session to keep alive
    assert admin_api.get(f"/api/v1/stream-sessions/{sid}/hls/index.m3u8").status_code == 404
    assert viewer.post(f"/api/v1/stream-sessions/{sid}/keepalive").status_code == 200
    other = make_user(db, "vw2", Role.VIEWER, [world.campus_id])
    assert other is not None
    viewer2 = Api()
    viewer2.login("vw2")
    assert viewer2.delete(f"/api/v1/stream-sessions/{sid}").status_code == 404
    assert admin_api.delete(f"/api/v1/stream-sessions/{sid}").status_code == 204  # admins may end any session


def test_keepalive_extends_and_an_expired_session_is_gone(admin_api: Api, world: World, db: Any) -> None:
    sid = start(admin_api, world.cams[0]).json()["id"]
    sess = db.get(StreamSession, uuid.UUID(sid))
    sess.expires_at = utcnow() + timedelta(seconds=2)
    db.commit()
    assert admin_api.post(f"/api/v1/stream-sessions/{sid}/keepalive").status_code == 200
    db.expire_all()
    assert db.get(StreamSession, uuid.UUID(sid)).expires_at > utcnow() + timedelta(seconds=30)
    db.get(StreamSession, uuid.UUID(sid)).expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    gone = admin_api.post(f"/api/v1/stream-sessions/{sid}/keepalive")
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "stream_session_expired"
    assert admin_api.delete(f"/api/v1/stream-sessions/{sid}").status_code == 204  # ending twice is harmless


# ----------------------------------------------------------------------------------------------- gateway failures
def test_gateway_down_gives_a_clear_error_and_no_dangling_session(
    admin_api: Api, world: World, db: Any, monkeypatch
) -> None:
    patched = get_settings().model_copy(update={"mediamtx_api_url": "http://127.0.0.1:1"})
    monkeypatch.setattr(mediamtx, "get_settings", lambda: patched)
    r = start(admin_api, world.cams[0])
    assert r.status_code == 503 and r.json()["error"]["code"] == "streaming_unavailable"
    assert db.scalars(select(StreamSession)).all() == []


def test_gateway_rejection_never_echoes_its_body_which_may_contain_credentials(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx
) -> None:
    mediamtx.api_status = 400
    mediamtx.api_body = f'{{"error": "bad source rtsp://viewer user:{PW}@10.0.0.5"}}'.encode()
    r = start(admin_api, world.cams[0])
    assert r.status_code == 502 and "HTTP 400" in r.json()["error"]["message"]
    assert PW not in r.text and "10.0.0.5" not in r.text


def test_replacing_an_existing_path_applies_current_credentials(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx, db: Any
) -> None:
    start(admin_api, world.cams[0])
    world.nvr.password_encrypted = encrypt_secret("new-pass")
    db.commit()
    start(admin_api, world.cams[0])
    assert "new-pass" in mediamtx.paths[f"raw-{world.cams[0].id.hex}-sub"]["source"]
    assert ("POST", f"/v3/config/paths/replace/raw-{world.cams[0].id.hex}-sub") in mediamtx.calls


def test_editing_or_deleting_an_nvr_ends_its_streams_and_removes_stale_paths(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx, db: Any
) -> None:
    sid = start(admin_api, world.cams[0]).json()["id"]
    assert mediamtx.paths
    body = {"campus_id": str(world.campus_id), "name": world.nvr.name, "connection_type": "lan", "host": "10.0.0.5",
            "http_port": 80, "rtsp_port": 554, "username": "viewer user", "password": "rotated", "enabled": True}  # fmt: skip
    assert admin_api.put(f"/api/v1/nvrs/{world.nvr.id}", json=body).status_code == 200
    assert mediamtx.paths == {}
    db.expire_all()
    assert db.get(StreamSession, uuid.UUID(sid)).status == StreamSessionStatus.ENDED

    world.nvr.status, world.nvr.last_success_at = NvrStatus.CONNECTED, utcnow()
    db.commit()
    start(admin_api, world.cams[0])
    assert admin_api.delete(f"/api/v1/nvrs/{world.nvr.id}").status_code == 204
    assert mediamtx.paths == {}


# ----------------------------------------------------------------------------------------------- HLS proxy
def hls(api: Api, sid: str, file: str = "index.m3u8") -> Any:
    return api.get(f"/api/v1/stream-sessions/{sid}/hls/{file}")


def test_hls_proxy_serves_playlists_and_segments_for_an_authorised_session(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx
) -> None:
    sid = start(admin_api, world.cams[0]).json()["id"]
    pl = hls(admin_api, sid)
    assert pl.status_code == 200 and pl.text.startswith("#EXTM3U")
    assert (
        pl.headers["content-type"].startswith("application/vnd.apple.mpegurl")
        and pl.headers["cache-control"] == "no-store"
    )
    seg = hls(admin_api, sid, "seg0.mp4")
    assert seg.status_code == 200 and seg.headers["content-type"] == "video/mp4"
    assert mediamtx.hls_requests[0] == f"/live-{world.cams[0].id.hex}-sub/index.m3u8?cookieCheck=1"  # no 302 round trip


@pytest.mark.parametrize(
    "file",
    [
        "..%2f..%2fv3%2fconfig%2fpaths%2flist",
        "secret.txt",
        "a/b/c.m3u8",
        "..",
        "index.m3u8%00.txt",
        ".hidden.m3u8",
        "x.m3u8%3Fa=b",
    ],
)
def test_hls_proxy_only_serves_plain_hls_file_names(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx, file: str
) -> None:
    sid = start(admin_api, world.cams[0]).json()["id"]
    r = hls(admin_api, sid, file)
    # 404 from the proxy, or 405 when the client/router normalises ".." away before the proxy is even reached
    assert r.status_code in (404, 405)
    assert mediamtx.hls_requests == []  # nothing reached the gateway


def test_only_a_well_formed_session_parameter_is_forwarded_to_the_gateway(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx
) -> None:
    sid = start(admin_api, world.cams[0]).json()["id"]
    base = f"/api/v1/stream-sessions/{sid}/hls/index.m3u8"
    admin_api.get(base + "?x=1&path=other")
    assert mediamtx.hls_requests[-1] == f"/live-{world.cams[0].id.hex}-sub/index.m3u8?cookieCheck=1"
    muxer = "91f80856-74ba-4ed4-b15d-c29be3aa9469"
    assert admin_api.get(base + f"?session={muxer}&x=1").status_code == 200
    assert mediamtx.hls_requests[-1].endswith(
        f"?cookieCheck=1&session={muxer}"
    )  # MediaMTX needs this to find the muxer
    admin_api.get(base + "?session=../../v3/config/paths/list")
    assert mediamtx.hls_requests[-1].endswith("index.m3u8?cookieCheck=1")  # malformed values never reach the gateway


def test_hls_needs_a_login_and_the_right_session(admin_api: Api, world: World) -> None:
    sid = start(admin_api, world.cams[0]).json()["id"]
    assert Api().get(f"/api/v1/stream-sessions/{sid}/hls/index.m3u8").status_code == 401
    assert hls(admin_api, str(uuid.uuid4())).status_code == 404


def test_hls_stops_when_the_session_ends_or_expires(admin_api: Api, world: World, db: Any) -> None:
    sid = start(admin_api, world.cams[0]).json()["id"]
    assert hls(admin_api, sid).status_code == 200
    admin_api.delete(f"/api/v1/stream-sessions/{sid}")
    gone = hls(admin_api, sid)
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "stream_session_expired"


def test_revoking_campus_access_cuts_off_a_running_stream(world: World, db: Any) -> None:
    user = make_user(db, "vw", Role.VIEWER, [world.campus_id])
    viewer = Api()
    viewer.login("vw")
    sid = start(viewer, world.cams[0]).json()["id"]
    assert hls(viewer, sid).status_code == 200
    from app.models import UserCampusPermission

    db.query(UserCampusPermission).filter(UserCampusPermission.user_id == user.id).delete()
    db.commit()
    assert (
        hls(viewer, sid).status_code == 404
        and viewer.post(f"/api/v1/stream-sessions/{sid}/keepalive").status_code == 404
    )


def test_hls_reports_stream_not_ready_and_gateway_errors_separately(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx, monkeypatch
) -> None:
    sid = start(admin_api, world.cams[0]).json()["id"]
    mediamtx.hls_status = 404
    nr = hls(admin_api, sid)
    assert nr.status_code == 404 and nr.json()["error"]["code"] == "stream_not_ready"
    mediamtx.hls_status = 500
    assert hls(admin_api, sid).json()["error"]["code"] == "streaming_error"
    mediamtx.hls_status = None
    from app.api.v1 import streams

    patched = get_settings().model_copy(update={"mediamtx_hls_url": "http://127.0.0.1:1"})
    monkeypatch.setattr(streams, "get_settings", lambda: patched)
    down = hls(admin_api, sid)
    assert down.status_code == 503 and down.json()["error"]["code"] == "streaming_unavailable"


# ----------------------------------------------------------------------------------------------- admin and dashboard
def test_admin_overview_and_status(admin_api: Api, world: World, db: Any) -> None:
    start(admin_api, world.cams[0])
    rows = admin_api.get("/api/v1/stream-sessions").json()
    assert (
        len(rows) == 1
        and rows[0]["camera_name"] == "Cam 1"
        and rows[0]["username"] == "admin"
        and rows[0]["transcoded"] is True
    )
    st = admin_api.get("/api/v1/streaming/status").json()
    assert st["gateway_reachable"] is True and st["active_sessions"] == 1 and st["max_streams_per_nvr"] == 8
    assert admin_api.get("/api/v1/dashboard/summary").json()["active_streams"] == 1
    make_user(db, "vw", Role.VIEWER, [world.campus_id])
    v = Api()
    v.login("vw")
    assert v.get("/api/v1/stream-sessions").status_code == 403 and v.get("/api/v1/streaming/status").status_code == 403
    assert v.get("/api/v1/dashboard/summary").json()["active_streams"] == 1


def test_status_reports_an_unreachable_gateway(admin_api: Api, monkeypatch) -> None:
    patched = get_settings().model_copy(update={"mediamtx_api_url": "http://127.0.0.1:1"})
    monkeypatch.setattr(mediamtx, "get_settings", lambda: patched)
    assert admin_api.get("/api/v1/streaming/status").json()["gateway_reachable"] is False


# ----------------------------------------------------------------------------------------------- favorites and layouts
def test_favorites_are_per_user_idempotent_and_scoped(admin_api: Api, world: World, db: Any) -> None:
    cam = world.cams[0]
    assert admin_api.put(f"/api/v1/cameras/{cam.id}/favorite").status_code == 204
    assert admin_api.put(f"/api/v1/cameras/{cam.id}/favorite").status_code == 204
    items = admin_api.get("/api/v1/cameras").json()["items"]
    assert {c["name"]: c["is_favorite"] for c in items} == {"Cam 1": True, "Cam 2": False, "Cam 3": False}
    assert [c["name"] for c in admin_api.get("/api/v1/cameras?favorites=true").json()["items"]] == ["Cam 1"]

    make_user(db, "vw", Role.VIEWER, [world.campus_id])
    v = Api()
    v.login("vw")
    assert [c["is_favorite"] for c in v.get("/api/v1/cameras").json()["items"]] == [
        False
    ] * 3  # not shared between users
    assert admin_api.delete(f"/api/v1/cameras/{cam.id}/favorite").status_code == 204
    assert admin_api.delete(f"/api/v1/cameras/{cam.id}/favorite").status_code == 204
    assert admin_api.get("/api/v1/cameras?favorites=true").json()["total"] == 0

    make_user(db, "out", Role.VIEWER, [make_campus(db, "Elsewhere").id])
    o = Api()
    o.login("out")
    assert o.put(f"/api/v1/cameras/{cam.id}/favorite").status_code == 404


def test_cameras_can_be_fetched_by_id_in_one_call(admin_api: Api, world: World) -> None:
    want = [str(world.cams[2].id), str(world.cams[0].id)]
    got = admin_api.get("/api/v1/cameras?" + "&".join(f"ids={i}" for i in want)).json()
    assert {c["id"] for c in got["items"]} == set(want)


def test_layouts_crud_validation_and_isolation(admin_api: Api, world: World, db: Any) -> None:
    ids = [str(c.id) for c in world.cams]
    r = admin_api.post("/api/v1/layouts", json={"name": "  Entrances ", "grid_size": 9, "camera_ids": ids})
    assert r.status_code == 201 and r.json()["name"] == "Entrances" and r.json()["camera_ids"] == ids
    lid = r.json()["id"]
    assert admin_api.post("/api/v1/layouts", json={"name": "ENTRANCES", "grid_size": 4}).status_code == 409
    for bad in (
        {"name": "x", "grid_size": 7},
        {"name": " ", "grid_size": 4},
        {"name": "x", "grid_size": 4, "camera_ids": ["nope"]},
    ):
        assert admin_api.post("/api/v1/layouts", json=bad).status_code == 422
    assert (
        admin_api.post(
            "/api/v1/layouts", json={"name": "big", "grid_size": 4, "camera_ids": [str(uuid.uuid4())] * 101}
        ).status_code
        == 422
    )

    upd = admin_api.put(f"/api/v1/layouts/{lid}", json={"name": "Entrances 2", "grid_size": 4, "camera_ids": ids[:1]})
    assert upd.status_code == 200 and upd.json()["grid_size"] == 4 and upd.json()["camera_ids"] == ids[:1]
    assert [x["name"] for x in admin_api.get("/api/v1/layouts").json()] == ["Entrances 2"]

    make_user(db, "vw", Role.VIEWER, [world.campus_id])
    v = Api()
    v.login("vw")
    assert v.get("/api/v1/layouts").json() == []  # layouts are personal
    assert v.put(f"/api/v1/layouts/{lid}", json={"name": "mine now", "grid_size": 1}).status_code == 404
    assert v.delete(f"/api/v1/layouts/{lid}").status_code == 404
    assert admin_api.delete(f"/api/v1/layouts/{lid}").status_code == 204
    assert admin_api.get("/api/v1/layouts").json() == []


def test_the_real_gateway_client_is_never_used_by_tests(mediamtx: FakeMediaMtx) -> None:
    assert (
        get_settings().mediamtx_api_url == mediamtx.url
        and httpx.get(mediamtx.url + "/v3/paths/list").status_code == 200
    )


def test_a_redirect_from_the_gateway_is_followed(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx, monkeypatch
) -> None:
    """If a gateway version insists on its cookie-check redirect, the proxy must still deliver the playlist."""
    sid = start(admin_api, world.cams[0]).json()["id"]
    from app.api.v1 import streams

    real = streams.httpx.AsyncClient.build_request

    def without_marker(self, method, url, **kw):  # simulate a request that arrives without the marker parameter
        kw.pop("params", None)
        return real(self, method, url, **kw)

    monkeypatch.setattr(streams.httpx.AsyncClient, "build_request", without_marker)
    r = hls(admin_api, sid)
    assert r.status_code == 200 and r.text.startswith("#EXTM3U")
    assert mediamtx.hls_requests[-2].endswith("index.m3u8") and mediamtx.hls_requests[-1].endswith("cookieCheck=1")


def test_conversion_settings_are_pinned_per_stream_kind(admin_api: Api, world: World, mediamtx: FakeMediaMtx) -> None:
    """Unpinned FFmpeg threading saturated the CPU at about six streams on the test machine; see paths.py."""
    start(admin_api, world.cams[0], "sub")
    start(admin_api, world.cams[0], "main")
    sub = mediamtx.paths[f"live-{world.cams[0].id.hex}-sub"]["runOnDemand"]
    main = mediamtx.paths[f"live-{world.cams[0].id.hex}-main"]["runOnDemand"]
    for cmd in (sub, main):
        assert (
            "-threads" in cmd.split("-i ")[0] and "-preset veryfast" in cmd and "-an" in cmd
        )  # decoder and encoder pinned
    assert "-threads 1 -rtsp_transport" in sub and "-crf 28" in sub and "-maxrate 700k" in sub
    assert "-threads 2 -rtsp_transport" in main and "-crf 25" in main and "-maxrate 3000k" in main
    assert mediamtx.paths[f"live-{world.cams[0].id.hex}-sub"]["runOnDemandCloseAfter"] == "5s"


def start_hevc(api: Api, cam: Camera, stream: str = "sub") -> Any:
    return api.post(f"/api/v1/cameras/{cam.id}/stream-session", json={"stream": stream, "native_hevc": True})


def test_a_browser_that_plays_h265_gets_the_cameras_own_stream_with_no_conversion(
    admin_api: Api, world: World, mediamtx: FakeMediaMtx
) -> None:
    out = start_hevc(admin_api, world.cams[0]).json()
    assert out["transcoded"] is False
    nat = f"nat-{world.cams[0].id.hex}-sub"
    assert list(mediamtx.paths) == [nat]  # one pull from the NVR, no FFmpeg path
    assert "runOnDemand" not in mediamtx.paths[nat] and mediamtx.paths[nat]["source"].startswith("rtsp://")
    assert out["hls_url"].endswith("/hls/index.m3u8") and mediamtx.hls_requests == []
    assert hls(admin_api, out["id"]).status_code == 200
    assert mediamtx.hls_requests[-1].startswith(f"/{nat}/index.m3u8")  # the proxy serves the native path


def test_converted_and_native_viewers_of_one_camera_use_separate_paths_and_both_count_for_the_nvr(
    admin_api: Api, world: World, db: Any, mediamtx: FakeMediaMtx
) -> None:
    a = start(admin_api, world.cams[0]).json()  # old browser: converted
    b = start_hevc(admin_api, world.cams[0]).json()  # new browser: native
    assert a["transcoded"] is True and b["transcoded"] is False and a["id"] != b["id"]
    assert {
        f"live-{world.cams[0].id.hex}-sub",
        f"nat-{world.cams[0].id.hex}-sub",
        f"raw-{world.cams[0].id.hex}-sub",
    } == set(mediamtx.paths)
    assert manager.active_sessions_for_nvr(db, world.nvr.id) == 2  # two upstream sessions on the NVR


def test_h264_cameras_are_never_converted_whatever_the_browser_can_do(
    db: Any, admin_api: Api, mediamtx: FakeMediaMtx
) -> None:
    w = make_world(db, codec="H.264", campus_name="h264-native")
    for hevc in (False, True):
        out = admin_api.post(f"/api/v1/cameras/{w.cams[0].id}/stream-session", json={"native_hevc": hevc}).json()
        assert out["transcoded"] is False
    assert list(mediamtx.paths) == [f"live-{w.cams[0].id.hex}-sub"]


def test_unknown_codec_is_converted_even_for_hevc_browsers(admin_api: Api, db: Any, world: World) -> None:
    db.query(CameraStreamProfile).delete()
    db.commit()
    assert start_hevc(admin_api, world.cams[0]).json()["transcoded"] is True


@pytest.mark.parametrize(
    ("mode", "client_hevc", "codec", "expected"),
    [("auto", True, "H.265", False), ("auto", False, "H.265", True), ("auto", True, "H.264", False), ("auto", True, "HEVC", False),
     ("auto", True, "MJPEG", True), ("auto", True, None, True), ("always", True, "H.265", True), ("never", False, "H.265", False)],
)  # fmt: skip
def test_transcode_decision_table(mode: str, client_hevc: bool, codec: str | None, expected: bool) -> None:
    from app.streaming import paths

    assert paths.needs_transcode(codec, mode, client_hevc) is expected


def test_live_info_reflects_what_the_browser_can_play(admin_api: Api, world: World) -> None:
    cam = world.cams[0]
    assert admin_api.get(f"/api/v1/cameras/{cam.id}/live").json()["transcode"] is True
    assert admin_api.get(f"/api/v1/cameras/{cam.id}/live?hevc=true").json()["transcode"] is False
