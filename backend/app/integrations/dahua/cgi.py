"""Dahua HTTP CGI client (digest auth). Everything here was observed working on a DHI-NVR2116HS-4KS3,
firmware 4.005.0000000.7.R, on 2026-10-21 (see docs/nvr-capabilities.md). Other models are untested."""

import re
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

import httpx

from app.integrations.base import (
    CancelledDownload,
    ConnectionParams,
    NvrAuthError,
    NvrProtocolError,
    NvrTransferError,
    NvrUnreachableError,
)


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
        if resp.status_code == 403:
            # Observed on the test NVR while its admin account was locked after repeated failed logins.
            raise NvrAuthError(
                "The NVR refused access (HTTP 403). The account is most likely locked after failed logins: "
                "wait for the lock to expire or unlock it on the NVR. Do not retry meanwhile."
            )
        return resp

    def download(
        self,
        path: str,
        dest: Path,
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        """Stream a GET response to a file in 256 KiB chunks (a recording can be a gigabyte). The partial file is
        removed on any failure, so an incomplete download is never mistaken for a finished one."""
        written = 0
        expected = ""
        seen: list[httpx.Response] = []
        try:
            with self._client.stream("GET", self._base + path, timeout=httpx.Timeout(30.0, connect=5.0)) as resp:
                seen.append(resp)
                if resp.status_code in (401, 403):
                    raise NvrAuthError("The NVR refused the download (credentials rejected or account locked).")
                if resp.status_code != 200:
                    raise NvrProtocolError(f"The NVR answered the download with HTTP {resp.status_code}")
                expected = resp.headers.get("content-length", "")
                with dest.open("wb") as fh:
                    for chunk in resp.iter_bytes(262144):
                        if cancelled is not None and cancelled():
                            raise CancelledDownload()
                        fh.write(chunk)
                        written += len(chunk)
                        if progress is not None:
                            progress(written)
        except httpx.RemoteProtocolError as exc:
            dest.unlink(missing_ok=True)
            received = seen[0].num_bytes_downloaded if seen else written
            raise NvrTransferError(
                f"The NVR closed the connection after {received} of {expected or 'an unknown number of'} bytes"
            ) from exc
        except httpx.TimeoutException as exc:
            dest.unlink(missing_ok=True)
            raise NvrUnreachableError("The NVR stopped sending data during the download.") from exc
        except httpx.HTTPError as exc:
            dest.unlink(missing_ok=True)
            raise NvrUnreachableError(f"Network error during the download ({type(exc).__name__}).") from exc
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return written

    def get_kv(self, path: str) -> dict[str, str]:
        resp = self.get(path)
        if resp.status_code != 200:
            raise NvrProtocolError(f"Unexpected HTTP {resp.status_code} from the NVR for {path.split('?')[0]}")
        return parse_kv(resp.text)
