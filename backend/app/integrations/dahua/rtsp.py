"""Minimal RTSP DESCRIBE probe (digest auth) used to check that a channel is live without pulling media."""

import hashlib
import re
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class RtspProbeResult:
    status: int  # RTSP status code, 0 when we could not even connect
    detail: str


def _digest(user: str, password: str, method: str, uri: str, challenge: dict[str, str]) -> str:
    ha1 = hashlib.md5(f"{user}:{challenge['realm']}:{password}".encode()).hexdigest()  # noqa: S324 (protocol-mandated)
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()  # noqa: S324
    resp = hashlib.md5(f"{ha1}:{challenge['nonce']}:{ha2}".encode()).hexdigest()  # noqa: S324
    return (
        f'Digest username="{user}", realm="{challenge["realm"]}", nonce="{challenge["nonce"]}", '
        f'uri="{uri}", response="{resp}"'
    )


def _read_response(sock: socket.socket) -> tuple[int, str]:
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head = data.decode("latin-1", "replace")
    m = re.match(r"RTSP/1\.\d (\d{3})", head)
    return (int(m.group(1)) if m else 0), head


def rtsp_describe(host: str, port: int, user: str, password: str, path: str, timeout: float = 4.0) -> RtspProbeResult:
    uri = f"rtsp://{host}:{port}{path}"
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            base = f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: {{cseq}}\r\nAccept: application/sdp\r\n{{auth}}\r\n"
            sock.sendall(base.format(cseq=1, auth="").encode())
            status, head = _read_response(sock)
            if status == 401:
                m = re.search(r"WWW-Authenticate:\s*Digest\s+(.*)", head, re.I)
                if not m:
                    return RtspProbeResult(401, "RTSP requires authentication we cannot answer")
                challenge = {k.lower(): a or b for k, a, b in re.findall(r'(\w+)=(?:"([^"]*)"|([^\s,]+))', m.group(1))}
                if "realm" not in challenge or "nonce" not in challenge:
                    return RtspProbeResult(401, "Unsupported RTSP authentication challenge")
                auth = f"Authorization: {_digest(user, password, 'DESCRIBE', uri, challenge)}\r\n"
                sock.sendall(base.format(cseq=2, auth=auth).encode())
                status, head = _read_response(sock)
            return RtspProbeResult(status, f"RTSP {status}")
    except TimeoutError:
        return RtspProbeResult(0, "RTSP timed out")
    except OSError as exc:
        return RtspProbeResult(0, f"RTSP connection failed ({type(exc).__name__})")
