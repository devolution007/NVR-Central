"""SIMULATED MediaMTX: its control API (config paths) and HLS endpoint on one port. Tests only.

It records every path configuration it receives so tests can check what the backend sends (and, importantly, what it
does NOT send), and it can be told to misbehave."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PLAYLIST = b"#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:1\n#EXTINF:1.0,\nseg0.mp4\n"


class FakeMediaMtx:
    def __init__(self) -> None:
        self.paths: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.api_status: int | None = None  # force every API call to this status
        self.api_body: bytes = b""
        self.hls_status: int | None = None  # force every HLS request to this status
        self.hls_requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: Any) -> None:
                pass

            def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _api(self, method: str) -> None:
                outer.calls.append((method, self.path))
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if outer.api_status is not None:
                    return self._send(outer.api_status, outer.api_body)
                m = re.match(r"^/v3/config/paths/(add|replace|delete)/(.+)$", self.path)
                if m:
                    action, name = m.group(1), m.group(2)
                    exists = name in outer.paths
                    if action == "delete":
                        if not exists:
                            return self._send(404, b"{}")
                        del outer.paths[name]
                        return self._send(200, b"{}")
                    if action == "replace" and not exists:
                        return self._send(404, b"{}")
                    if action == "add" and exists:
                        return self._send(400, b"{}")
                    outer.paths[name] = json.loads(raw or b"{}")
                    return self._send(200, b"{}")
                if self.path.startswith("/v3/paths/list"):
                    return self._send(200, json.dumps({"itemCount": 0, "pageCount": 0, "items": []}).encode())
                self._send(404, b"{}")

            def do_POST(self) -> None:  # noqa: N802
                self._api("POST")

            def do_DELETE(self) -> None:  # noqa: N802
                self._api("DELETE")

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/v3/"):
                    return self._api("GET")
                outer.hls_requests.append(self.path)
                if outer.hls_status is not None:
                    return self._send(outer.hls_status, b"nope", "text/plain")
                if "cookieCheck=1" not in self.path:  # the real server redirects the first request like this
                    self.send_response(302)
                    self.send_header("Location", self.path + ("&" if "?" in self.path else "?") + "cookieCheck=1")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                m = re.match(r"^/([^/]+)/([^?]+)", self.path)
                if not m or m.group(1) not in outer.paths:
                    return self._send(404, b"not found", "text/plain")
                if m.group(2).endswith(".m3u8"):
                    return self._send(200, PLAYLIST, "application/vnd.apple.mpegurl")
                self._send(200, b"\x00\x00\x00\x18ftypmp42", "video/mp4")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port: int = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def reset(self) -> None:
        self.paths.clear()
        self.calls.clear()
        self.hls_requests.clear()
        self.api_status = self.hls_status = None
        self.api_body = b""

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
