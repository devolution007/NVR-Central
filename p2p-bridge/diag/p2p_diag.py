"""Phase 0 P2P diagnostic for Dahua NVRs (uses OpenDahua 1.0.8).

Reports what works over Dahua P2P, with serial/password/MAC values redacted.
Run:  python diag/p2p_diag.py --channel 0 --hours 24 [--cgi] [--download]

Credentials come from environment variables or p2p-bridge/.env:
NVR_SERIAL, NVR_USERNAME, NVR_PASSWORD (password prompt if missing).

Step statuses are "ok" (observed working on this run), "failed" or "skipped".
Nothing is marked "tested" in the app until it is confirmed on real hardware
and recorded in docs/p2p-compatibility.md.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Type

from opendahua import DahuaNVR
from opendahua.api.api_request import T
from opendahua.api_dahua.request.api_request_dahua import ApiRequestDahua
from opendahua.api_dahua.response.api_response_dahua import ApiResponseDahua
from opendahua.http.http_request_body import HttpRequestBody
from opendahua.http.http_request_method import HttpRequestMethod
from opendahua.object.url import Url

ROOT = Path(__file__).resolve().parent.parent
SENSITIVE_KEY = re.compile(r"serial|password|passwd|mac|sn$|token|sign", re.I)

# Read-only CGI endpoints to probe through the P2P HTTP tunnel (port 80).
# EXPERIMENTAL: these are standard Dahua HTTP API paths, not part of OpenDahua's
# public API; results tell us whether channel discovery can ride the tunnel.
CGI_PROBES = [
    "/cgi-bin/magicBox.cgi?action=getDeviceType",
    "/cgi-bin/magicBox.cgi?action=getSystemInfo",
    "/cgi-bin/magicBox.cgi?action=getSoftwareVersion",
    "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle",
    "/cgi-bin/LogicDeviceManager.cgi?action=getCameraAll",
    "/cgi-bin/configManager.cgi?action=getConfig&name=RemoteDevice",
    "/cgi-bin/recordManager.cgi?action=getCaps",
]


class _RawResponse(ApiResponseDahua):
    def __init__(self, data: dict) -> None:
        self.data = data

    @classmethod
    def parse(cls, value: dict) -> "_RawResponse":
        return cls(value)


class _RawCgiRequest(ApiRequestDahua[_RawResponse]):
    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    def determine_endpoint(self) -> Url:
        return Url(self._endpoint)

    def get_request_method(self) -> HttpRequestMethod:
        return HttpRequestMethod.GET

    def determine_body_or_none(self) -> HttpRequestBody | None:
        return None

    def get_response_class(self) -> Type[T]:  # type: ignore[override]
        return _RawResponse  # type: ignore[return-value]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def redact(obj: Any, secrets: list[str]) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if SENSITIVE_KEY.search(str(k)) else redact(v, secrets)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, secrets) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    if isinstance(obj, str):
        for s in secrets:
            if s:
                obj = obj.replace(s, "***")
        return obj
    return obj


def mask_serial(serial: str) -> str:
    return f"***{serial[-4:]}" if len(serial) > 4 else "***"


class Report:
    def __init__(self, secrets: list[str]) -> None:
        self.steps: list[dict] = []
        self.secrets = secrets

    async def run(self, name: str, coro_fn, *, skip: str | None = None):
        if skip:
            self.steps.append({"step": name, "status": "skipped", "reason": skip})
            print(f"[SKIP] {name}: {skip}")
            return None
        t0 = time.monotonic()
        try:
            result = await coro_fn()
            dt = round(time.monotonic() - t0, 2)
            self.steps.append({"step": name, "status": "ok", "seconds": dt, "detail": redact(result, self.secrets)})
            print(f"[ OK ] {name} ({dt}s)")
            return result
        except Exception as exc:  # noqa: BLE001 - diagnostic tool reports everything
            dt = round(time.monotonic() - t0, 2)
            msg = redact(f"{type(exc).__name__}: {exc}", self.secrets)
            tb = redact(traceback.format_exc(limit=4), self.secrets)
            self.steps.append({"step": name, "status": "failed", "seconds": dt, "error": msg, "trace": tb})
            print(f"[FAIL] {name} ({dt}s): {msg}")
            return None


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    serial = os.environ.get("NVR_SERIAL") or input("NVR serial: ").strip()
    user = os.environ.get("NVR_USERNAME", "admin")
    password = os.environ.get("NVR_PASSWORD") or getpass.getpass("NVR password: ")
    rpt = Report([serial, password])
    started = datetime.now().astimezone()
    print(f"Target serial: {mask_serial(serial)}  user: {user}  python: {sys.version.split()[0]}")

    nvr = DahuaNVR(serial, user, password)
    connected = False

    async def connect():
        await asyncio.wait_for(nvr.connect(), timeout=args.timeout)
        return {"connected": True}

    if await rpt.run("p2p_connect", connect) is not None:
        connected = True
    no_session = None if connected else "no P2P session"

    async def name():
        return {"name": await nvr.get_nvr_name()}

    async def clock():
        t = await nvr.get_time()
        return {"nvr_time_local": t.isoformat(), "pc_time": datetime.now().isoformat()}

    await rpt.run("device_name", name, skip=no_session)
    await rpt.run("device_time", clock, skip=no_session)

    # --- CGI probes (channel discovery / capabilities) ---
    for ep in CGI_PROBES:
        label = "cgi:" + ep.split("/cgi-bin/")[1]

        async def probe(ep=ep):
            r = await asyncio.wait_for(nvr._client.send_request(_RawCgiRequest(ep)), timeout=args.timeout)  # noqa: SLF001
            return r.data

        await rpt.run(label, probe, skip=no_session or (None if args.cgi else "use --cgi"))

    # --- Recording search ---
    videos: list = []

    async def search():
        nonlocal videos
        start = datetime.now() - timedelta(hours=args.hours)
        videos = await asyncio.wait_for(nvr.get_videos(args.channel, start), timeout=args.timeout * 4)
        info: dict[str, Any] = {"channel_param": args.channel, "hours": args.hours, "count": len(videos)}
        if videos:
            info["first_start"] = videos[0].get_time_start().isoformat()
            info["last_end"] = videos[-1].get_time_end().isoformat()
            info["file_types"] = sorted({str(v.get_file_type()) for v in videos})
        return info

    await rpt.run(f"recording_search(channel={args.channel})", search, skip=no_session)

    # --- Download (whole file is buffered in RAM by OpenDahua 1.0.8) ---
    async def download():
        v = min(videos, key=lambda x: x.get_time_end() - x.get_time_start())
        out = ROOT / "downloads"
        t0 = time.monotonic()
        path = await asyncio.wait_for(nvr.download_video(v, out), timeout=args.timeout * 10)
        size = path.stat().st_size
        dt = time.monotonic() - t0
        info: dict[str, Any] = {
            "bytes": size,
            "mbit_per_s": round(size * 8 / 1e6 / max(dt, 0.01), 2),
            "duration_s": round((v.get_time_end() - v.get_time_start()).total_seconds()),
        }
        if shutil.which("ffprobe"):
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", str(path)],
                capture_output=True, text=True,
            )
            info["ffprobe"] = json.loads(p.stdout) if p.returncode == 0 and p.stdout else {"error": p.stderr[:300]}
        else:
            info["ffprobe"] = "ffprobe not installed"
        return info

    dl_skip = no_session
    if dl_skip is None and not args.download:
        dl_skip = "use --download"
    elif dl_skip is None and not videos:
        dl_skip = "no recordings found in window"
    await rpt.run("recording_download(shortest segment)", download, skip=dl_skip)

    if connected:
        try:
            await nvr.disconnect()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] disconnect: {type(exc).__name__}: {exc}")

    out = ROOT / "reports" / f"diag_{started:%Y%m%d_%H%M%S}.json"
    out.write_text(
        json.dumps({"started": started.isoformat(), "target": mask_serial(serial), "opendahua": "1.0.8", "steps": rpt.steps}, indent=2),
        encoding="utf-8",
    )
    print(f"\nReport written: {out}")
    ok = sum(s["status"] == "ok" for s in rpt.steps)
    bad = sum(s["status"] == "failed" for s in rpt.steps)
    print(f"Summary: {ok} ok, {bad} failed, {len(rpt.steps) - ok - bad} skipped")
    return 0 if connected else 2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", type=int, default=0, help="channel number passed unchanged to OpenDahua (indexing unverified: try 0 and 1)")
    ap.add_argument("--hours", type=float, default=24, help="search window ending now")
    ap.add_argument("--cgi", action="store_true", help="probe read-only CGI endpoints for device info / channel discovery")
    ap.add_argument("--download", action="store_true", help="download the shortest found segment (buffered in RAM)")
    ap.add_argument("--timeout", type=float, default=45, help="per-step timeout in seconds")
    args = ap.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
