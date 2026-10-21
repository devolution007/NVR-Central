"""Phase 0b LAN diagnostic for Dahua NVRs (no Dahua-specific library).

Talks to the NVR by IP (LAN, or through a VPN/tunnel) using only:
  * HTTP CGI with digest auth   (device info, channels, stream config, search, download)
  * RTSP over TCP with digest   (live main/sub streams, concurrency, playback)
It reports what the NVR really answers, with serial/MAC/password masked.

Run from a PC that can reach the NVR:
    python diag/lan_diag.py --channels 1,2 --streams 1,2,4,8 --hours 24 [--download] [--playback]

Settings come from environment variables or p2p-bridge/.env:
    NVR_HOST (IP or name), NVR_USERNAME, NVR_PASSWORD
    optional: NVR_HTTP_PORT (80), NVR_RTSP_PORT (554)

Everything here is READ-ONLY on the NVR (search, snapshot, download, live view).
Step statuses are "ok" / "failed" / "skipped". Nothing becomes "verified" in the
app until the result is recorded in docs/nvr-capabilities.md.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parent.parent
SENSITIVE_KEY = re.compile(r"serial|password|passwd|mac|sn$|token|sign|secret", re.I)
DAHUA_TIME = "%Y-%m-%d %H:%M:%S"

CGI_PROBES = [
    ("device_type", "/cgi-bin/magicBox.cgi?action=getDeviceType"),
    ("system_info", "/cgi-bin/magicBox.cgi?action=getSystemInfo"),
    ("software_version", "/cgi-bin/magicBox.cgi?action=getSoftwareVersion"),
    ("current_time", "/cgi-bin/global.cgi?action=getCurrentTime"),
    ("time_zone_config", "/cgi-bin/configManager.cgi?action=getConfig&name=Locales"),
    ("channel_titles", "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"),
    ("camera_all", "/cgi-bin/LogicDeviceManager.cgi?action=getCameraAll"),
    ("remote_devices", "/cgi-bin/configManager.cgi?action=getConfig&name=RemoteDevice"),
    ("encode_config", "/cgi-bin/configManager.cgi?action=getConfig&name=Encode"),
    ("record_caps", "/cgi-bin/recordManager.cgi?action=getCaps"),
    ("video_in_caps", "/cgi-bin/devVideoInput.cgi?action=getCaps&channel=1"),
]


# ---------------------------------------------------------------- helpers
def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def redact(obj: Any, secret_values: list[str]) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if SENSITIVE_KEY.search(str(k)) else redact(v, secret_values)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, secret_values) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    if isinstance(obj, str):
        for s in secret_values:
            if s:
                obj = obj.replace(s, "***")
        return obj
    return obj


def mask_host(host: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", host)
    return f"x.x.x.{m.group(4)}" if m else "<hostname>"


def parse_kv(text: str) -> dict[str, str]:
    """Parse Dahua 'key=value' CRLF text into a flat dict."""
    out: dict[str, str] = {}
    for line in text.replace("\r", "").split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def group_items(flat: dict[str, str]) -> list[dict[str, str]]:
    items: dict[int, dict[str, str]] = {}
    for k, v in flat.items():
        m = re.match(r"items\[(\d+)\]\.(.+)", k)
        if m:
            items.setdefault(int(m.group(1)), {})[m.group(2)] = v
    return [items[i] for i in sorted(items)]


def parse_digest_challenge(header: str) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2) or m.group(3) for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^\s,]+))', header)}


def digest_response(user: str, password: str, method: str, uri: str, ch: dict[str, str]) -> str:
    """RFC 2617 digest (MD5), with qop=auth when the server offers it."""
    ha1 = hashlib.md5(f"{user}:{ch['realm']}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    if "auth" in ch.get("qop", ""):
        cnonce, nc = secrets.token_hex(8), "00000001"
        resp = hashlib.md5(f"{ha1}:{ch['nonce']}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()
        extra = f', qop=auth, nc={nc}, cnonce="{cnonce}"'
    else:
        resp = hashlib.md5(f"{ha1}:{ch['nonce']}:{ha2}".encode()).hexdigest()
        extra = ""
    return (f'Digest username="{user}", realm="{ch["realm"]}", nonce="{ch["nonce"]}", '
            f'uri="{uri}", response="{resp}"{extra}')


# ---------------------------------------------------------------- RTSP
class RtspError(Exception):
    pass


class RtspClient:
    """Minimal RTSP-over-TCP client: DESCRIBE / SETUP / PLAY / TEARDOWN, digest auth."""

    def __init__(self, host: str, port: int, user: str, password: str) -> None:
        self.host, self.port, self.user, self.password = host, port, user, password
        self.cseq = 0
        self.challenge: dict[str, str] | None = None
        self.session: str | None = None
        self.reader: asyncio.StreamReader
        self.writer: asyncio.StreamWriter

    async def connect(self, timeout: float) -> None:
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout)

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    async def request(self, method: str, uri: str, headers: dict[str, str] | None = None, timeout: float = 10) -> tuple[int, dict[str, str], bytes]:
        for attempt in range(2):
            self.cseq += 1
            h = {"CSeq": str(self.cseq), "User-Agent": "nvr-central-diag"}
            if self.session:
                h["Session"] = self.session
            if self.challenge:
                h["Authorization"] = digest_response(self.user, self.password, method, uri, self.challenge)
            h.update(headers or {})
            msg = f"{method} {uri} RTSP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n"
            self.writer.write(msg.encode())
            await self.writer.drain()
            head = await asyncio.wait_for(self.reader.readuntil(b"\r\n\r\n"), timeout)
            lines = head.decode("latin-1").split("\r\n")
            status = int(lines[0].split(" ")[1])
            rh = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ":" in ln)}
            body = b""
            if int(rh.get("content-length", "0")) > 0:
                body = await asyncio.wait_for(self.reader.readexactly(int(rh["content-length"])), timeout)
            if status == 401 and attempt == 0 and "www-authenticate" in rh and "digest" in rh["www-authenticate"].lower():
                self.challenge = parse_digest_challenge(rh["www-authenticate"])
                continue
            return status, rh, body
        raise RtspError("authentication failed (401 after digest retry)")

    async def stream_probe(self, url: str, seconds: float, timeout: float) -> dict[str, Any]:
        """DESCRIBE, SETUP the first video track over TCP-interleaved, PLAY, count bytes."""
        info: dict[str, Any] = {}
        st, rh, sdp = await self.request("DESCRIBE", url, {"Accept": "application/sdp"}, timeout)
        info["describe_status"] = st
        if st != 200:
            raise RtspError(f"DESCRIBE returned {st}")
        text = sdp.decode("latin-1", "replace")
        codec = re.search(r"a=rtpmap:\d+ ([A-Za-z0-9\-]+)/", text)
        info["codec"] = codec.group(1) if codec else "unknown"
        vsec = text.split("m=video", 1)[1] if "m=video" in text else text
        ctrl = re.search(r"a=control:(\S+)", vsec)
        base = rh.get("content-base", url).rstrip("/")
        control = ctrl.group(1) if ctrl else "trackID=0"
        track = control if control.startswith("rtsp://") else f"{base}/{control}"
        st, rh, _ = await self.request("SETUP", track, {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"}, timeout)
        info["setup_status"] = st
        if st != 200:
            raise RtspError(f"SETUP returned {st}")
        self.session = rh.get("session", "").split(";")[0] or None
        t0 = time.monotonic()
        st, _, _ = await self.request("PLAY", url, {"Range": "npt=0.000-"}, timeout)
        info["play_status"] = st
        if st != 200:
            raise RtspError(f"PLAY returned {st}")
        total, first = 0, None
        end = t0 + seconds
        while time.monotonic() < end:
            try:
                chunk = await asyncio.wait_for(self.reader.read(65536), max(0.1, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if not chunk:
                info["closed_by_server"] = True
                break
            if first is None:
                first = time.monotonic() - t0
            total += len(chunk)
        dt = max(time.monotonic() - t0, 0.01)
        info.update(bytes=total, seconds=round(dt, 1), kbit_per_s=round(total * 8 / 1000 / dt), first_data_s=round(first, 2) if first is not None else None)
        try:
            await self.request("TEARDOWN", url, timeout=3)
        except Exception:  # noqa: BLE001
            pass
        if total == 0:
            raise RtspError("PLAY accepted but no media received")
        return info


# ---------------------------------------------------------------- report
class Report:
    def __init__(self, secret_values: list[str]) -> None:
        self.steps: list[dict] = []
        self.secrets = secret_values

    async def run(self, name: str, fn, *, skip: str | None = None):
        if skip:
            self.steps.append({"step": name, "status": "skipped", "reason": skip})
            print(f"[SKIP] {name}: {skip}")
            return None
        t0 = time.monotonic()
        try:
            result = await fn()
            dt = round(time.monotonic() - t0, 2)
            self.steps.append({"step": name, "status": "ok", "seconds": dt, "detail": redact(result, self.secrets)})
            print(f"[ OK ] {name} ({dt}s)")
            return result if result is not None else True
        except Exception as exc:  # noqa: BLE001
            dt = round(time.monotonic() - t0, 2)
            msg = redact(f"{type(exc).__name__}: {exc}", self.secrets)
            self.steps.append({"step": name, "status": "failed", "seconds": dt, "error": msg,
                               "trace": redact(traceback.format_exc(limit=3), self.secrets)})
            print(f"[FAIL] {name} ({dt}s): {msg}")
            return None


# ---------------------------------------------------------------- main
async def main_async(a: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    host = os.environ.get("NVR_HOST") or input("NVR host/IP: ").strip()
    user = os.environ.get("NVR_USERNAME", "admin")
    password = os.environ.get("NVR_PASSWORD") or getpass.getpass("NVR password: ")
    http_port = int(os.environ.get("NVR_HTTP_PORT", "80"))
    rtsp_port = int(os.environ.get("NVR_RTSP_PORT", "554"))
    rpt = Report([password, host])
    started = datetime.now().astimezone()
    print(f"Target: {mask_host(host)}  user: {user}  http:{http_port} rtsp:{rtsp_port}  python: {sys.version.split()[0]}")

    # 1. Port reachability
    async def port_check():
        res = {}
        for name, port in (("http", http_port), ("rtsp", rtsp_port), ("dahua_37777", 37777)):
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(host, port), a.timeout)
                w.close()
                res[name] = "open"
            except Exception as e:  # noqa: BLE001
                res[name] = f"closed/unreachable ({type(e).__name__})"
        if res["http"] != "open":
            raise RuntimeError(f"HTTP port not reachable: {res}")
        return res

    reachable = await rpt.run("tcp_reachability", port_check) is not None
    no_net = None if reachable else "NVR not reachable"

    base = f"http://{host}:{http_port}"
    client = httpx.AsyncClient(auth=httpx.DigestAuth(user, password), timeout=a.timeout, follow_redirects=False)

    async def get(path: str) -> httpx.Response:
        return await client.get(base + path)

    # 2. CGI probes
    cgi_data: dict[str, dict[str, str]] = {}
    for label, path in CGI_PROBES:
        async def probe(path=path, label=label):
            r = await get(path)
            if r.status_code == 401:
                raise RuntimeError("401 unauthorized (credentials rejected or account locked)")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            kv = parse_kv(r.text)
            cgi_data[label] = kv
            return {"keys": len(kv), "sample": dict(list(kv.items())[:25])}
        await rpt.run(f"cgi:{label}", probe, skip=no_net)

    # 3. Channel summary from what the NVR returned
    async def channel_summary():
        titles = {k: v for k, v in cgi_data.get("channel_titles", {}).items() if k.endswith(".Name")}
        remote = {k for k in cgi_data.get("remote_devices", {}) if k.startswith("table.RemoteDevice[")}
        return {"channel_title_entries": len(titles), "titles": titles, "remote_device_keys": len(remote),
                "camera_all_keys": len(cgi_data.get("camera_all", {}))}
    await rpt.run("channel_summary", channel_summary, skip=no_net if not cgi_data else None)

    # 4. Snapshot
    async def snapshot():
        r = await get(f"/cgi-bin/snapshot.cgi?channel={a.channels[0]}")
        if r.status_code != 200 or not r.content.startswith(b"\xff\xd8"):
            raise RuntimeError(f"HTTP {r.status_code}, not a JPEG ({r.headers.get('content-type')})")
        return {"bytes": len(r.content)}
    await rpt.run(f"snapshot(channel={a.channels[0]})", snapshot, skip=no_net)

    # 5. RTSP: single-stream describe/play per channel, main + sub
    def rtsp_url(ch: int, sub: int) -> str:
        return f"rtsp://{host}:{rtsp_port}/cam/realmonitor?channel={ch}&subtype={sub}"

    async def one_stream(ch: int, sub: int, seconds: float) -> dict[str, Any]:
        c = RtspClient(host, rtsp_port, user, password)
        try:
            await c.connect(a.timeout)
            return await c.stream_probe(rtsp_url(ch, sub), seconds, a.timeout)
        finally:
            await c.close()

    rtsp_ok = False
    for ch in a.channels:
        for sub, nm in ((1, "sub"), (0, "main")):
            res = await rpt.run(f"rtsp_live(channel={ch},{nm})", lambda ch=ch, sub=sub: one_stream(ch, sub, 4), skip=no_net)
            rtsp_ok = rtsp_ok or res is not None

    # 6. RTSP concurrency (substreams, cycling through channels)
    for n in a.streams:
        async def many(n=n):
            tasks = [one_stream(a.channels[i % len(a.channels)], 1, 6) for i in range(n)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            good = [r for r in results if isinstance(r, dict)]
            bad = [f"{type(r).__name__}: {r}" for r in results if not isinstance(r, dict)]
            out = {"requested": n, "succeeded": len(good), "failed": len(bad),
                   "total_kbit_per_s": sum(r["kbit_per_s"] for r in good), "errors": sorted(set(bad))[:5]}
            if bad:
                raise RuntimeError(json.dumps(out))
            return out
        await rpt.run(f"rtsp_concurrent_substreams(n={n})", many, skip=no_net or (None if rtsp_ok else "single RTSP stream failed"))

    # 7. Recording search via mediaFileFind
    found: list[dict[str, str]] = []
    search_channel_used: dict[str, Any] = {}

    async def search(ch: int):
        end = datetime.now()
        start = end - timedelta(hours=a.hours)
        r = await get("/cgi-bin/mediaFileFind.cgi?action=factory.create")
        if r.status_code != 200 or "result=" not in r.text:
            raise RuntimeError(f"factory.create -> HTTP {r.status_code}")
        obj = parse_kv(r.text)["result"]
        try:
            q = (f"/cgi-bin/mediaFileFind.cgi?action=findFile&object={obj}&condition.Channel={ch}"
                 f"&condition.StartTime={quote(start.strftime(DAHUA_TIME))}&condition.EndTime={quote(end.strftime(DAHUA_TIME))}"
                 f"&condition.Types[0]=dav&condition.Flags[0]=Timing")
            r = await get(q)
            if r.status_code != 200:
                raise RuntimeError(f"findFile -> HTTP {r.status_code} {r.text[:80]!r}")
            items: list[dict[str, str]] = []
            while True:
                r = await get(f"/cgi-bin/mediaFileFind.cgi?action=findNextFile&object={obj}&count=100")
                kv = parse_kv(r.text)
                batch = group_items(kv)
                items += batch
                if r.status_code != 200 or len(batch) < 100:
                    break
            return items
        finally:
            for act in ("close", "destroy"):
                try:
                    await get(f"/cgi-bin/mediaFileFind.cgi?action={act}&object={obj}")
                except Exception:  # noqa: BLE001
                    pass

    for ch in sorted(set([0, *a.channels])):
        async def do_search(ch=ch):
            items = await search(ch)
            if items and not found:
                found.extend(items)
                search_channel_used["channel"] = ch
            info: dict[str, Any] = {"channel_param": ch, "hours": a.hours, "count": len(items)}
            if items:
                info["first"] = {k: v for k, v in items[0].items() if k in ("StartTime", "EndTime", "Type", "VideoStream", "Length", "Channel", "Flags[0]")}
                info["streams_seen"] = sorted({i.get("VideoStream", "?") for i in items})
                info["types_seen"] = sorted({i.get("Type", "?") for i in items})
            return info
        await rpt.run(f"recording_search(channel_param={ch})", do_search, skip=no_net)

    # 8. Download (streamed to disk, capped)
    async def download():
        def dur(i):
            try:
                return datetime.strptime(i["EndTime"], DAHUA_TIME) - datetime.strptime(i["StartTime"], DAHUA_TIME)
            except Exception:  # noqa: BLE001
                return timedelta.max
        item = min(found, key=dur)
        out_dir = ROOT / "downloads"
        out_dir.mkdir(exist_ok=True)
        dest = out_dir / ("segment_" + re.sub(r"\W", "", item["StartTime"]) + ".dav")
        cap = a.max_mb * 1024 * 1024
        t0, size, capped = time.monotonic(), 0, False
        async with client.stream("GET", base + "/cgi-bin/RPC_Loadfile" + item["FilePath"]) as r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes(65536):
                    f.write(chunk)
                    size += len(chunk)
                    if size >= cap:
                        capped = True
                        break
        dt = max(time.monotonic() - t0, 0.01)
        info: dict[str, Any] = {"bytes": size, "mbit_per_s": round(size * 8 / 1e6 / dt, 2), "capped": capped,
                                "segment_seconds": round(dur(item).total_seconds()) if dur(item) != timedelta.max else None,
                                "memory_note": "streamed to disk in 64 KiB chunks"}
        if shutil.which("ffprobe"):
            p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", str(dest)],
                               capture_output=True, text=True)
            info["ffprobe"] = json.loads(p.stdout) if p.returncode == 0 and p.stdout else {"error": p.stderr[:200]}
        else:
            info["ffprobe"] = "ffprobe not installed"
        return info

    dl_skip = no_net or (None if a.download else "use --download")
    if dl_skip is None and not found:
        dl_skip = "no recordings found by search"
    await rpt.run("recording_download(shortest segment)", download, skip=dl_skip)

    # 9. RTSP playback (alternative export path)
    async def playback():
        item = min(found, key=lambda i: i.get("StartTime", ""))
        st = datetime.strptime(item["StartTime"], DAHUA_TIME)
        et = min(datetime.strptime(item["EndTime"], DAHUA_TIME), st + timedelta(seconds=30))
        ch = search_channel_used.get("channel") or a.channels[0]
        url = (f"rtsp://{host}:{rtsp_port}/cam/playback?channel={ch}&subtype=0"
               f"&starttime={st:%Y_%m_%d_%H_%M_%S}&endtime={et:%Y_%m_%d_%H_%M_%S}")
        c = RtspClient(host, rtsp_port, user, password)
        try:
            await c.connect(a.timeout)
            return await c.stream_probe(url, 5, a.timeout)
        finally:
            await c.close()

    pb_skip = no_net or (None if a.playback else "use --playback")
    if pb_skip is None and not found:
        pb_skip = "no recordings found by search"
    await rpt.run("rtsp_playback", playback, skip=pb_skip)

    await client.aclose()

    out = ROOT / "reports" / f"lan_{started:%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({"started": started.isoformat(), "target": mask_host(host), "steps": rpt.steps}, indent=2), encoding="utf-8")
    ok = sum(s["status"] == "ok" for s in rpt.steps)
    bad = sum(s["status"] == "failed" for s in rpt.steps)
    print(f"\nReport written: {out}\nSummary: {ok} ok, {bad} failed, {len(rpt.steps) - ok - bad} skipped")
    return 0 if reachable else 2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channels", default="1", help="comma list of channels to test (Dahua RTSP/CGI numbering, normally 1-based)")
    ap.add_argument("--streams", default="1,2,4", help="comma list of concurrent substream counts to test (use 0 to skip)")
    ap.add_argument("--hours", type=float, default=24, help="recording search window ending now")
    ap.add_argument("--download", action="store_true", help="download the shortest found segment")
    ap.add_argument("--max-mb", type=int, default=50, help="stop a download after this many MB")
    ap.add_argument("--playback", action="store_true", help="test RTSP playback of the first found segment (30 s)")
    ap.add_argument("--timeout", type=float, default=10, help="per-request timeout in seconds")
    a = ap.parse_args()
    a.channels = [int(x) for x in a.channels.split(",") if x.strip()]
    a.streams = [int(x) for x in a.streams.split(",") if x.strip() and int(x) > 0]
    sys.exit(asyncio.run(main_async(a)))


if __name__ == "__main__":
    main()
