"""Dahua adapter that talks to the NVR directly over HTTP CGI + RTSP (LAN or through the campus tunnel)."""

import re
from datetime import UTC, datetime
from urllib.parse import quote

from app.integrations.base import (
    CameraProbe,
    CapabilityResult,
    ChannelInfo,
    ConnectionParams,
    DeviceInfo,
    NvrAdapter,
    NvrAuthError,
    NvrProtocolError,
    NvrUnsupportedError,
    StreamProfile,
)
from app.integrations.dahua.cgi import DahuaHttp, group_indexed
from app.integrations.dahua.rtsp import rtsp_describe
from app.integrations.dahua.timezones import DAHUA_TZ_OFFSET_MINUTES, VERIFIED_CODES, offset_from_clock
from app.models.enums import CapabilityName, CapabilityStatus, StreamKind

DAHUA_TIME = "%Y-%m-%d %H:%M:%S"


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def _is_real_address(addr: str | None) -> bool:
    return bool(addr) and addr not in ("0.0.0.0", "")


def parse_profile(enc: dict[str, str], index: int, fmt: str, stream: StreamKind) -> StreamProfile | None:
    base = f"table.Encode[{index}].{fmt}[0].Video."
    codec = enc.get(base + "Compression")
    if codec is None:
        return None
    width, height = _int(enc.get(base + "Width")), _int(enc.get(base + "Height"))
    res = enc.get(base + "resolution", "")
    m = re.match(r"^(\d+)x(\d+)$", res)
    if m and (not width or not height):
        width, height = int(m.group(1)), int(m.group(2))
    return StreamProfile(
        stream=stream, codec=codec, width=width, height=height,
        bitrate_kbps=_int(enc.get(base + "BitRate")), fps=_int(enc.get(base + "FPS")),
    )  # fmt: skip


class DahuaDirectAdapter(NvrAdapter):
    def __init__(self, params: ConnectionParams) -> None:
        self._p = params
        self._http: DahuaHttp | None = None
        self._first_channel: int | None = None

    # -- session --------------------------------------------------------------------------------------------
    def connect(self) -> None:
        self._http = DahuaHttp(self._p)
        try:
            kv = self._http.get_kv("/cgi-bin/magicBox.cgi?action=getDeviceType")
        except Exception:
            self.disconnect()
            raise
        if "type" not in kv:
            self.disconnect()
            raise NvrProtocolError("The device answered but does not look like a Dahua NVR")

    def disconnect(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    @property
    def http(self) -> DahuaHttp:
        if self._http is None:
            raise NvrProtocolError("Not connected")
        return self._http

    # -- info -----------------------------------------------------------------------------------------------
    def get_device_info(self) -> DeviceInfo:
        model = self.http.get_kv("/cgi-bin/magicBox.cgi?action=getDeviceType").get("type")
        firmware = None
        try:
            firmware = self.http.get_kv("/cgi-bin/magicBox.cgi?action=getSoftwareVersion").get("version")
        except NvrProtocolError:
            pass

        warnings: list[str] = []
        device_time: datetime | None = None
        offset: int | None = None
        try:
            raw = self.http.get_kv("/cgi-bin/global.cgi?action=getCurrentTime").get("result")
            if raw:
                device_time = datetime.strptime(raw, DAHUA_TIME)
                offset = offset_from_clock(device_time, datetime.now(UTC))
        except (NvrProtocolError, ValueError):
            warnings.append("Could not read the NVR clock, so its timezone offset is unknown")

        tz_code: int | None = None
        try:
            tz_code = _int(self.http.get_kv("/cgi-bin/configManager.cgi?action=getConfig&name=NTP").get("table.NTP.TimeZone"))
        except NvrProtocolError:
            pass
        if offset is not None and tz_code is not None and tz_code in DAHUA_TZ_OFFSET_MINUTES:
            expected = DAHUA_TZ_OFFSET_MINUTES[tz_code]
            if expected != offset:
                note = "" if tz_code in VERIFIED_CODES else " (zone table not hardware-verified for this code)"
                warnings.append(
                    f"NVR clock suggests UTC{offset / 60:+.2f} but its timezone setting says UTC{expected / 60:+.2f}. "
                    f"The NVR clock may be wrong{note}."
                )
        return DeviceInfo(model=model, firmware=firmware, timezone_code=tz_code, device_time=device_time,
                          utc_offset_minutes=offset, warnings=tuple(warnings))  # fmt: skip

    # -- channels -------------------------------------------------------------------------------------------
    def list_cameras(self) -> list[ChannelInfo]:
        titles = {
            int(m.group(1)): v
            for k, v in self.http.get_kv("/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle").items()
            if (m := re.match(r"^table\.ChannelTitle\[(\d+)\]\.Name$", k))
        }
        resp = self.http.get("/cgi-bin/LogicDeviceManager.cgi?action=getCameraAll")
        if resp.status_code != 200:
            raise NvrUnsupportedError("This NVR does not report which channels have cameras (getCameraAll unavailable)")
        from app.integrations.dahua.cgi import parse_kv

        entries = group_indexed(parse_kv(resp.text), "camera")
        enc = self.http.get_kv("/cgi-bin/configManager.cgi?action=getConfig&name=Encode")

        channels: list[ChannelInfo] = []
        capacity = len(titles) or len(entries)
        for idx in sorted(entries):
            if idx >= capacity:
                continue  # trailing bookkeeping entry past the NVR's channel count
            e = entries[idx]
            if not _is_real_address(e.get("DeviceInfo.Address")):
                continue  # empty or disabled slot, not a camera
            profiles = tuple(
                p
                for p in (
                    parse_profile(enc, idx, "MainFormat", StreamKind.MAIN),
                    parse_profile(enc, idx, "ExtraFormat", StreamKind.SUB),
                )
                if p is not None
            )
            channels.append(
                ChannelInfo(
                    channel_number=idx + 1,  # NVR config is 0-based, CGI/RTSP are 1-based
                    name=titles.get(idx) or f"Channel {idx + 1}",
                    model=e.get("DeviceInfo.DeviceType") or None,
                    profiles=profiles,
                )
            )
        if channels:
            self._first_channel = channels[0].channel_number
        return channels

    def get_camera_status(self, channel_number: int) -> CameraProbe:
        r = rtsp_describe(self._p.host, self._p.rtsp_port, self._p.username, self._p.password,
                          f"/cam/realmonitor?channel={channel_number}&subtype=1")  # fmt: skip
        return CameraProbe(channel_number=channel_number, online=r.status == 200, detail=r.detail)

    # -- capabilities ---------------------------------------------------------------------------------------
    def get_capabilities(self) -> list[CapabilityResult]:
        T, U, K = CapabilityStatus.SUPPORTED_TESTED, CapabilityStatus.UNSUPPORTED, CapabilityStatus.UNKNOWN
        ch = self._first_channel or 1
        out = [
            CapabilityResult(CapabilityName.P2P_AUTH, K, "Not used: this NVR is reached over a tunnel or LAN address"),
            CapabilityResult(CapabilityName.DEVICE_INFO, T, "Model, firmware and clock read over HTTP"),
        ]

        r = self.http.get("/cgi-bin/LogicDeviceManager.cgi?action=getCameraAll")
        out.append(CapabilityResult(CapabilityName.CHANNEL_DISCOVERY, T if r.status_code == 200 else U,
                                    "getCameraAll answered" if r.status_code == 200 else f"getCameraAll returned HTTP {r.status_code}"))  # fmt: skip

        probe = rtsp_describe(self._p.host, self._p.rtsp_port, self._p.username, self._p.password,
                              f"/cam/realmonitor?channel={ch}&subtype=1")  # fmt: skip
        out.append(CapabilityResult(CapabilityName.LIVE_VIDEO, T if probe.status == 200 else K,
                                    f"RTSP substream of channel {ch} answered" if probe.status == 200
                                    else f"RTSP check on channel {ch} failed: {probe.detail}. Not proven unsupported"))  # fmt: skip

        out.append(self._probe_search(ch))

        out.append(CapabilityResult(CapabilityName.RECORDING_DOWNLOAD, K,
                                    "Not tested yet. A download is only attempted by a backup or manual export"))  # fmt: skip

        snap = self.http.get(f"/cgi-bin/snapshot.cgi?channel={ch}")
        if snap.status_code == 200 and snap.content[:2] == b"\xff\xd8":
            out.append(CapabilityResult(CapabilityName.SNAPSHOT, T, f"JPEG snapshot of channel {ch} received"))
        else:
            out.append(CapabilityResult(CapabilityName.SNAPSHOT, K, f"Snapshot of channel {ch} failed (HTTP {snap.status_code})"))
        return out

    def _probe_search(self, channel: int) -> CapabilityResult:
        T, U, K = CapabilityStatus.SUPPORTED_TESTED, CapabilityStatus.UNSUPPORTED, CapabilityStatus.UNKNOWN
        name = CapabilityName.RECORDING_SEARCH
        created = self.http.get("/cgi-bin/mediaFileFind.cgi?action=factory.create")
        if created.status_code == 404:
            return CapabilityResult(name, U, "This NVR has no mediaFileFind interface")
        if created.status_code != 200 or "result=" not in created.text:
            return CapabilityResult(name, K, f"Search session could not be created (HTTP {created.status_code})")
        obj = created.text.split("result=", 1)[1].strip().split()[0]
        try:
            now = datetime.now()
            end, start = now.strftime(DAHUA_TIME), datetime.fromtimestamp(now.timestamp() - 3600).strftime(DAHUA_TIME)
            r = self.http.get(
                f"/cgi-bin/mediaFileFind.cgi?action=findFile&object={obj}&condition.Channel={channel}"
                f"&condition.StartTime={quote(start)}&condition.EndTime={quote(end)}&condition.Types[0]=dav"
            )
            if r.status_code == 200:
                return CapabilityResult(name, T, f"Search on channel {channel} succeeded (results not counted here)")
            return CapabilityResult(name, K, f"Search on channel {channel} returned HTTP {r.status_code}")
        finally:
            for action in ("close", "destroy"):
                try:
                    self.http.get(f"/cgi-bin/mediaFileFind.cgi?action={action}&object={obj}")
                except (NvrProtocolError, NvrAuthError, OSError):
                    pass
