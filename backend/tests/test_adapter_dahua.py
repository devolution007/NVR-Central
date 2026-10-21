"""Dahua direct adapter against the SIMULATED NVR (tests/support/fake_dahua.py)."""

import socket
from collections.abc import Iterator

import pytest

from app.integrations import hostguard
from app.integrations.base import (
    ConnectionParams,
    NvrAuthError,
    NvrProtocolError,
    NvrUnreachableError,
    NvrUnsupportedError,
)
from app.integrations.dahua.cgi import DahuaHttp, group_indexed, parse_kv
from app.integrations.dahua.direct import DahuaDirectAdapter, parse_profile
from app.integrations.dahua.p2p import DahuaP2pAdapter
from app.models.enums import CapabilityName, CapabilityStatus, StreamKind
from tests.support.fake_dahua import FakeDahuaServer, SimCamera, SimNvr


@pytest.fixture
def sim() -> Iterator[tuple[SimNvr, FakeDahuaServer]]:
    nvr = SimNvr(
        capacity=4,
        cameras=[
            SimCamera("Lobby", "DH-IPC-A"),
            SimCamera("Gate", "DH-IPC-B", rtsp_ok=False),  # configured but not answering
            SimCamera("Disabled slot", enable=False),
        ],
    )
    server = FakeDahuaServer(nvr).start()
    yield nvr, server
    server.stop()


def params(server: FakeDahuaServer, password: str = "secret") -> ConnectionParams:
    return ConnectionParams("127.0.0.1", server.http_port, server.rtsp_port, "admin", password)


def caps(adapter: DahuaDirectAdapter) -> dict[CapabilityName, CapabilityStatus]:
    return {c.name: c.status for c in adapter.get_capabilities()}


def test_parsers_handle_dahua_key_value_text() -> None:
    flat = parse_kv("a=1\r\nb=two=three\r\nnoequals\r\n")
    assert flat == {"a": "1", "b": "two=three"}
    grouped = group_indexed({"camera[0].X": "a", "camera[2].Y.Z": "b", "other[1].X": "c"}, "camera")
    assert grouped == {0: {"X": "a"}, 2: {"Y.Z": "b"}}


def test_parse_profile_reads_resolution_and_codec() -> None:
    enc = {
        "table.Encode[0].MainFormat[0].Video.Compression": "H.265",
        "table.Encode[0].MainFormat[0].Video.resolution": "1920x1080",
        "table.Encode[0].MainFormat[0].Video.BitRate": "2048",
        "table.Encode[0].MainFormat[0].Video.FPS": "25",
    }
    p = parse_profile(enc, 0, "MainFormat", StreamKind.MAIN)
    assert p is not None and (p.codec, p.width, p.height, p.bitrate_kbps, p.fps) == ("H.265", 1920, 1080, 2048, 25)
    assert parse_profile(enc, 0, "ExtraFormat", StreamKind.SUB) is None
    assert parse_profile(enc, 5, "MainFormat", StreamKind.MAIN) is None


def test_device_info_model_firmware_and_timezone_offset(sim) -> None:
    nvr, server = sim
    with DahuaDirectAdapter(params(server)) as a:
        info = a.get_device_info()
    assert info.model == "DHI-NVR-SIMULATED"
    assert info.firmware.startswith("4.005")
    assert info.timezone_code == 7
    assert info.utc_offset_minutes == 300  # measured from the NVR clock, not looked up
    assert info.warnings == ()


def test_clock_and_timezone_setting_disagreement_is_reported_not_hidden(sim) -> None:
    nvr, server = sim
    nvr.clock_offset_minutes = 60  # clock says UTC+1, the zone setting still says code 7 = UTC+5
    with DahuaDirectAdapter(params(server)) as a:
        info = a.get_device_info()
    assert info.utc_offset_minutes == 60  # the measured value wins
    assert info.warnings and "clock" in info.warnings[0].lower()


def test_negative_and_half_hour_offsets_round_to_quarter_hours(sim) -> None:
    nvr, server = sim
    for minutes in (-300, 330, 345, 0):
        nvr.clock_offset_minutes = minutes
        with DahuaDirectAdapter(params(server)) as a:
            assert a.get_device_info().utc_offset_minutes == minutes


def test_list_cameras_maps_entries_to_one_based_channels_and_skips_empty_slots(sim) -> None:
    _, server = sim
    with DahuaDirectAdapter(params(server)) as a:
        chans = a.list_cameras()
    assert [(c.channel_number, c.name, c.model) for c in chans] == [(1, "Lobby", "DH-IPC-A"), (2, "Gate", "DH-IPC-B")]
    main = next(p for p in chans[0].profiles if p.stream == StreamKind.MAIN)
    sub = next(p for p in chans[0].profiles if p.stream == StreamKind.SUB)
    assert (main.width, main.height, sub.width, sub.height) == (1920, 1080, 704, 576)


def test_camera_status_uses_rtsp_describe_and_distinguishes_online_from_offline(sim) -> None:
    _, server = sim
    with DahuaDirectAdapter(params(server)) as a:
        assert a.get_camera_status(1).online is True
        gate = a.get_camera_status(2)
        assert gate.online is False and "404" in gate.detail
        assert a.get_camera_status(4).online is False


def test_capabilities_only_claim_what_was_observed(sim) -> None:
    _, server = sim
    with DahuaDirectAdapter(params(server)) as a:
        a.list_cameras()
        c = caps(a)
    T, K = CapabilityStatus.SUPPORTED_TESTED, CapabilityStatus.UNKNOWN
    assert c[CapabilityName.DEVICE_INFO] == T
    assert c[CapabilityName.CHANNEL_DISCOVERY] == T
    assert c[CapabilityName.LIVE_VIDEO] == T
    assert c[CapabilityName.RECORDING_SEARCH] == T
    assert c[CapabilityName.SNAPSHOT] == T
    # a connection succeeding must never imply downloads or P2P work
    assert c[CapabilityName.RECORDING_DOWNLOAD] == K
    assert c[CapabilityName.P2P_AUTH] == K


def test_failed_live_probe_is_unknown_not_unsupported(sim) -> None:
    nvr, server = sim
    nvr.cameras[0].rtsp_ok = False
    with DahuaDirectAdapter(params(server)) as a:
        a.list_cameras()
        assert caps(a)[CapabilityName.LIVE_VIDEO] == CapabilityStatus.UNKNOWN


def test_missing_interfaces_are_reported_as_unsupported(sim) -> None:
    nvr, server = sim
    nvr.has_media_find = False
    nvr.has_camera_all = False
    nvr.snapshot_ok = False
    with DahuaDirectAdapter(params(server)) as a:
        c = caps(a)
        with pytest.raises(NvrUnsupportedError):
            a.list_cameras()
    assert c[CapabilityName.RECORDING_SEARCH] == CapabilityStatus.UNSUPPORTED
    assert c[CapabilityName.CHANNEL_DISCOVERY] == CapabilityStatus.UNSUPPORTED
    assert c[CapabilityName.SNAPSHOT] == CapabilityStatus.UNKNOWN


def test_search_probe_always_closes_its_finder_session(sim) -> None:
    nvr, server = sim
    with DahuaDirectAdapter(params(server)) as a:
        a.get_capabilities()
    actions = [p.split("action=")[1].split("&")[0] for p in nvr.requests if "mediaFileFind" in p]
    assert actions[-2:] == ["close", "destroy"]


def test_wrong_password_raises_auth_error_without_leaking_it(sim) -> None:
    _, server = sim
    with pytest.raises(NvrAuthError) as exc:
        DahuaDirectAdapter(params(server, "wrong-password-xyz")).connect()
    assert "wrong-password-xyz" not in exc.value.message and "wrong-password-xyz" not in repr(
        params(server, "wrong-password-xyz")
    )


def test_non_dahua_device_is_a_protocol_error(sim) -> None:
    nvr, server = sim
    nvr.is_dahua = False
    with pytest.raises(NvrProtocolError):
        DahuaDirectAdapter(params(server)).connect()


def test_refused_connection_is_unreachable_and_fast() -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    with pytest.raises(NvrUnreachableError):
        DahuaDirectAdapter(ConnectionParams("127.0.0.1", port, 554, "admin", "x")).connect()


def test_slow_nvr_times_out_as_unreachable(sim) -> None:
    nvr, server = sim
    nvr.delay_seconds = 1.5
    http = DahuaHttp(params(server), timeout=0.3)
    with pytest.raises(NvrUnreachableError):
        http.get("/cgi-bin/magicBox.cgi?action=getDeviceType")
    http.close()


def test_p2p_adapter_is_honestly_disabled() -> None:
    with pytest.raises(NvrUnsupportedError, match="disabled"):
        DahuaP2pAdapter().connect()


# ----------------------------------------------------------------------------------------------- host guard
@pytest.mark.parametrize("host", ["169.254.169.254", "0.0.0.0", "224.0.0.1", "::1", "127.0.0.1"])
def test_hostguard_refuses_dangerous_targets(host, monkeypatch) -> None:
    monkeypatch.setattr(hostguard, "get_settings", lambda: type("S", (), {"allow_loopback_nvr_hosts": False})())
    with pytest.raises(hostguard.HostNotAllowedError):
        hostguard.assert_host_allowed(host)


@pytest.mark.parametrize("host", ["192.168.15.6", "10.77.3.6", "172.16.0.9"])
def test_hostguard_allows_private_lan_and_tunnel_addresses(host) -> None:
    hostguard.assert_host_allowed(host)


def test_hostguard_checks_what_a_hostname_resolves_to(monkeypatch) -> None:
    monkeypatch.setattr(hostguard, "get_settings", lambda: type("S", (), {"allow_loopback_nvr_hosts": False})())
    with pytest.raises(hostguard.HostNotAllowedError):
        hostguard.assert_host_allowed("localhost")
