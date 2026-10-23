"""SIMULATED Dahua NVR (HTTP CGI + RTSP). For automated tests only: it proves our code handles the response
formats we captured from a real DHI-NVR2116HS-4KS3, it does not prove any real device behaves this way.
"""

import asyncio
import hashlib
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

REALM, NONCE = "Login to SIMULATED", "sim-nonce-123"


@dataclass
class SimCamera:
    name: str
    model: str = "DH-IPC-SIM"
    address: str = "192.168.15.10"
    enable: bool = True
    rtsp_ok: bool = True


@dataclass
class SimRecording:
    channel: int  # 1-based
    stream: str  # "Main" or "Extra1", as the real NVR labels them
    start: datetime  # NVR local wall clock
    end: datetime
    data: bytes = b""
    reported_length: int | None = None  # what the NVR claims; defaults to len(data)

    @property
    def path(self) -> str:
        s = self.start.strftime("%H.%M.%S")
        e = self.end.strftime("%H.%M.%S")
        return f"/mnt/dvr/{self.start:%Y-%m-%d}/{0 if self.stream == 'Main' else 128}/dav/{self.start:%H}/0/0/1/{s}-{e}[R][0@0][0].dav"


@dataclass
class SimNvr:
    username: str = "admin"
    password: str = "secret"
    device_type: str = "DHI-NVR-SIMULATED"
    firmware: str = "4.005.0000000.7.R,build:2026-06-16"
    tz_code: int = 7
    clock_offset_minutes: int = 300  # NVR local time = UTC + this
    capacity: int = 4
    cameras: list[SimCamera] = field(default_factory=list)
    has_camera_all: bool = True
    has_media_find: bool = True
    snapshot_ok: bool = True
    is_dahua: bool = True
    locked: bool = False  # real NVRs answer 403 while the account is locked
    delay_seconds: float = 0.0
    requests: list[str] = field(default_factory=list)
    recordings: list[SimRecording] = field(default_factory=list)
    download_cut_after: int | None = None  # drop the connection after this many body bytes, once
    download_status: int = 200
    search_status: int = 200
    _cursor: int = 0
    _criteria: tuple[int, datetime, datetime] | None = None


def _digest_ok(method: str, auth: str | None, nvr: SimNvr) -> bool:
    if not auth or not auth.startswith("Digest"):
        return False
    d = {k: a or b for k, a, b in re.findall(r'(\w+)=(?:"([^"]*)"|([^\s,]+))', auth)}
    ha1 = hashlib.md5(f"{nvr.username}:{REALM}:{nvr.password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{d.get('uri', '')}".encode()).hexdigest()
    if "qop" in d:
        exp = hashlib.md5(f"{ha1}:{NONCE}:{d['nc']}:{d['cnonce']}:{d['qop']}:{ha2}".encode()).hexdigest()
    else:
        exp = hashlib.md5(f"{ha1}:{NONCE}:{ha2}".encode()).hexdigest()
    return d.get("response") == exp and d.get("username") == nvr.username


def _make_handler(nvr: SimNvr) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            nvr.requests.append(self.path)
            if nvr.delay_seconds:
                import time

                time.sleep(nvr.delay_seconds)
            if not nvr.is_dahua:
                return self._send(404)
            if nvr.locked:
                return self._send(403)
            if not _digest_ok("GET", self.headers.get("Authorization"), nvr):
                self.send_response(401)
                self.send_header("WWW-Authenticate", f'Digest realm="{REALM}", qop="auth", nonce="{NONCE}"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            u = urlparse(self.path)
            if u.path.startswith("/cgi-bin/RPC_Loadfile/"):
                return self._loadfile(unquote(u.path[len("/cgi-bin/RPC_Loadfile") :]))
            q = parse_qs(u.query)
            act = q.get("action", [""])[0]
            name = q.get("name", [""])[0]
            local = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=nvr.clock_offset_minutes)

            if u.path.endswith("magicBox.cgi"):
                body = {
                    "getDeviceType": f"type={nvr.device_type}\r\n",
                    "getSoftwareVersion": f"version={nvr.firmware}\r\n",
                }.get(act)
                return self._send(200, body.encode()) if body else self._send(404)
            if u.path.endswith("global.cgi"):
                return self._send(200, f"result={local:%Y-%m-%d %H:%M:%S}\r\n".encode())
            if u.path.endswith("configManager.cgi"):
                if name == "NTP":
                    return self._send(200, f"table.NTP.TimeZone={nvr.tz_code}\r\ntable.NTP.Enable=false\r\n".encode())
                if name == "ChannelTitle":
                    rows = "".join(f"table.ChannelTitle[{i}].Name=Ch{i + 1}\r\n" for i in range(nvr.capacity))
                    for i, c in enumerate(nvr.cameras[: nvr.capacity]):
                        rows = rows.replace(
                            f"table.ChannelTitle[{i}].Name=Ch{i + 1}", f"table.ChannelTitle[{i}].Name={c.name}"
                        )
                    return self._send(200, rows.encode())
                if name == "Encode":
                    rows = ""
                    for i in range(nvr.capacity):
                        rows += (
                            f"table.Encode[{i}].MainFormat[0].Video.Compression=H.265\r\n"
                            f"table.Encode[{i}].MainFormat[0].Video.resolution=1920x1080\r\n"
                            f"table.Encode[{i}].MainFormat[0].Video.Width=1920\r\n"
                            f"table.Encode[{i}].MainFormat[0].Video.Height=1080\r\n"
                            f"table.Encode[{i}].MainFormat[0].Video.BitRate=2048\r\n"
                            f"table.Encode[{i}].MainFormat[0].Video.FPS=25\r\n"
                            f"table.Encode[{i}].ExtraFormat[0].Video.Compression=H.265\r\n"
                            f"table.Encode[{i}].ExtraFormat[0].Video.resolution=704x576\r\n"
                            f"table.Encode[{i}].ExtraFormat[0].Video.BitRate=512\r\n"
                            f"table.Encode[{i}].ExtraFormat[0].Video.FPS=25\r\n"
                        )
                    return self._send(200, rows.encode())
                return self._send(200, f"table.{name}.x=1\r\n".encode())
            if u.path.endswith("LogicDeviceManager.cgi"):
                if not nvr.has_camera_all:
                    return self._send(404)
                rows = ""
                for i in range(nvr.capacity + 1):  # one trailing bookkeeping entry, as the real NVR returns
                    c = nvr.cameras[i] if i < len(nvr.cameras) else None
                    addr = c.address if c else "0.0.0.0"
                    rows += (
                        f"camera[{i}].Channel=0\r\n"
                        f"camera[{i}].DeviceInfo.Address={addr}\r\n"
                        f"camera[{i}].DeviceInfo.Enable={'true' if (c and c.enable) else 'false'}\r\n"
                        f"camera[{i}].DeviceInfo.DeviceType={c.model if c else ''}\r\n"
                    )
                return self._send(200, rows.encode())
            if u.path.endswith("snapshot.cgi"):
                return (
                    self._send(200, b"\xff\xd8\xff" + b"0" * 64, "image/jpeg") if nvr.snapshot_ok else self._send(500)
                )
            if u.path.endswith("mediaFileFind.cgi"):
                if not nvr.has_media_find:
                    return self._send(404)
                if act == "factory.create":
                    return self._send(200, b"result=77\r\n")
                if act == "findFile":
                    if nvr.search_status != 200:
                        return self._send(nvr.search_status)
                    nvr._cursor = 0
                    nvr._criteria = (
                        int(q["condition.Channel"][0]),
                        datetime.strptime(q["condition.StartTime"][0], "%Y-%m-%d %H:%M:%S"),
                        datetime.strptime(q["condition.EndTime"][0], "%Y-%m-%d %H:%M:%S"),
                    )
                    return self._send(200, b"OK\r\n")
                if act == "findNextFile" and nvr._criteria:
                    ch, lo, hi = nvr._criteria
                    hits = [r for r in nvr.recordings if r.channel == ch and r.start < hi and r.end > lo]
                    batch = hits[nvr._cursor : nvr._cursor + int(q.get("count", ["100"])[0])]
                    nvr._cursor += len(batch)
                    rows = f"found={len(batch)}\r\n"
                    for i, r in enumerate(batch):
                        rows += (
                            f"items[{i}].Channel={ch - 1}\r\nitems[{i}].StartTime={r.start:%Y-%m-%d %H:%M:%S}\r\n"
                            f"items[{i}].EndTime={r.end:%Y-%m-%d %H:%M:%S}\r\nitems[{i}].FilePath={r.path}\r\n"
                            f"items[{i}].Length={r.reported_length if r.reported_length is not None else len(r.data)}\r\n"
                            f"items[{i}].VideoStream={r.stream}\r\nitems[{i}].Type=dav\r\nitems[{i}].Flags[0]=Timing\r\n"
                        )
                    return self._send(200, rows.encode())
                return self._send(200, b"OK\r\n")
            return self._send(404)

        def _loadfile(self, path: str) -> None:
            if nvr.download_status != 200:
                return self._send(nvr.download_status)
            rec = next((r for r in nvr.recordings if r.path == path), None)
            if rec is None:
                return self._send(404)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(rec.data)))
            self.end_headers()
            cut = nvr.download_cut_after
            if cut is not None:
                nvr.download_cut_after = None
                self.wfile.write(rec.data[:cut])
                self.wfile.flush()
                self.close_connection = True
                self.connection.close()  # the NVR dies mid-file
                return
            self.wfile.write(rec.data)

    return Handler


class FakeDahuaServer:
    def __init__(self, nvr: SimNvr, http_port: int = 0, rtsp_port: int = 0) -> None:
        self.nvr = nvr
        self._rtsp_bind = rtsp_port
        self._http = ThreadingHTTPServer(("127.0.0.1", http_port), _make_handler(nvr))
        self.http_port: int = self._http.server_address[1]
        self.rtsp_port: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._stopped = False
        self._thread: threading.Thread | None = None

    def start(self) -> "FakeDahuaServer":
        threading.Thread(target=self._http.serve_forever, daemon=True).start()
        self._thread = threading.Thread(target=self._run_rtsp, daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._http.shutdown()
        self._http.server_close()
        if self._loop and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(timeout=3)

    def _run_rtsp(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    head = (await reader.readuntil(b"\r\n\r\n")).decode()
                    first = head.split("\r\n")[0].split(" ")
                    method, uri = first[0], first[1]
                    auth = next(
                        (
                            ln.split(":", 1)[1].strip()
                            for ln in head.split("\r\n")
                            if ln.lower().startswith("authorization")
                        ),
                        None,
                    )
                    cseq = next(
                        (ln.split(":", 1)[1].strip() for ln in head.split("\r\n") if ln.lower().startswith("cseq")), "0"
                    )
                    ch = re.search(r"channel=(\d+)", uri)
                    ch_idx = int(ch.group(1)) - 1 if ch else -1
                    if not _digest_ok(method, auth, self.nvr):
                        writer.write(
                            f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\nWWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}"\r\n\r\n'.encode()
                        )
                    else:
                        cams = self.nvr.cameras
                        ok = 0 <= ch_idx < len(cams) and cams[ch_idx].rtsp_ok and cams[ch_idx].enable
                        writer.write(
                            f"RTSP/1.0 {200 if ok else 404} {'OK' if ok else 'Not Found'}\r\nCSeq: {cseq}\r\n\r\n".encode()
                        )
                    await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        async def main() -> None:
            self._stop = asyncio.Event()
            server = await asyncio.start_server(handle, "127.0.0.1", self._rtsp_bind)
            self.rtsp_port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop.wait()
            server.close()
            await server.wait_closed()

        loop.run_until_complete(main())
        loop.close()
