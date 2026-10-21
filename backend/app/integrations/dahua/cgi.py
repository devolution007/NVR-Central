"""Dahua HTTP CGI client (digest auth). Everything here was observed working on a DHI-NVR2116HS-4KS3,
firmware 4.005.0000000.7.R, on 2026-10-21 (see docs/nvr-capabilities.md). Other models are untested."""

import re
from types import TracebackType

import httpx

from app.integrations.base import ConnectionParams, NvrAuthError, NvrProtocolError, NvrUnreachableError


def parse_kv(text: str) -> dict[str, str]:
    """Parse Dahua 'key=value' CRLF text into a flat dict."""
    out: dict[str, str] = {}
    for line in text.replace("\r", "").split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def group_indexed(flat: dict[str, str], prefix_re: str) -> dict[int, dict[str, str]]:
    """Group keys like 'camera[3].DeviceInfo.Address' into {3: {'DeviceInfo.Address': ...}}."""
    rx = re.compile(rf"^{prefix_re}\[(\d+)\]\.(.+)$")
    out: dict[int, dict[str, str]] = {}
    for key, value in flat.items():
        m = rx.match(key)
        if m:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = value
    return out


class DahuaHttp:
    """Thin sync client. Raises the adapter exception types so callers never see httpx errors."""

    def __init__(self, params: ConnectionParams, timeout: float = 10.0) -> None:
        self._base = f"http://{params.host}:{params.http_port}"
        self._client = httpx.Client(
            auth=httpx.DigestAuth(params.username, params.password),
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=False,
        )

    def __enter__(self) -> "DahuaHttp":
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(self, path: str) -> httpx.Response:
        try:
            resp = self._client.get(self._base + path)
        except httpx.ConnectTimeout as exc:
            raise NvrUnreachableError("Connection timed out. The NVR did not answer.") from exc
        except httpx.TimeoutException as exc:
            raise NvrUnreachableError("The NVR stopped responding (read timeout).") from exc
        except httpx.ConnectError as exc:
            raise NvrUnreachableError("Cannot connect to the NVR (refused or no route).") from exc
        except httpx.HTTPError as exc:
            raise NvrUnreachableError(f"Network error talking to the NVR ({type(exc).__name__}).") from exc
        if resp.status_code == 401:
            raise NvrAuthError("The NVR rejected the username or password (or the account is locked).")
        return resp

    def get_kv(self, path: str) -> dict[str, str]:
        resp = self.get(path)
        if resp.status_code != 200:
            raise NvrProtocolError(f"Unexpected HTTP {resp.status_code} from the NVR for {path.split('?')[0]}")
        return parse_kv(resp.text)
