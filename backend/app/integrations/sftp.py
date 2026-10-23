"""SFTP client for backups (paramiko). Host keys are pinned: a connection is refused unless the server's key matches
the SHA-256 fingerprint an administrator confirmed. Error messages are safe to show and never contain credentials."""

import base64
import contextlib
import hashlib
import io
import posixpath
import secrets
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

import paramiko

CHUNK = 262144


class SftpError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SftpUnreachableError(SftpError): ...


class SftpAuthError(SftpError): ...


class SftpHostKeyError(SftpError): ...


class SftpCancelled(SftpError): ...


@dataclass(frozen=True)
class SftpTarget:
    host: str
    port: int
    username: str
    password: str | None = field(default=None, repr=False)
    private_key: str | None = field(default=None, repr=False)
    base_path: str = "/"
    host_key_sha256: str | None = None  # pinned fingerprint; connecting without one is refused


@dataclass(frozen=True)
class HostKeyInfo:
    key_type: str
    sha256: str


def fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH style fingerprint, e.g. SHA256:abc... (what `ssh-keygen -lf` prints)."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _open_transport(host: str, port: int, timeout: float) -> paramiko.Transport:
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except TimeoutError as exc:
        raise SftpUnreachableError(f"Connection to {host}:{port} timed out") from exc
    except OSError as exc:
        raise SftpUnreachableError(f"Cannot connect to {host}:{port} (refused or no route)") from exc
    transport = paramiko.Transport(sock)
    transport.banner_timeout = timeout
    try:
        transport.start_client(timeout=timeout)
    except (paramiko.SSHException, EOFError, OSError) as exc:
        transport.close()
        raise SftpUnreachableError(
            f"{host}:{port} did not complete an SSH handshake. Is it an SFTP/SSH server?"
        ) from exc
    return transport


def fetch_host_key(host: str, port: int, timeout: float = 10.0) -> HostKeyInfo:
    """Read the server's host key without logging in, so an administrator can compare it with the real server's
    fingerprint before trusting it."""
    transport = _open_transport(host, port, timeout)
    try:
        key = transport.get_remote_server_key()
        return HostKeyInfo(key.get_name(), fingerprint(key))
    finally:
        transport.close()


def _load_private_key(text: str) -> paramiko.PKey:
    for loader in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return loader.from_private_key(io.StringIO(text))
        except paramiko.PasswordRequiredException as exc:
            raise SftpAuthError("The private key is protected by a passphrase, which is not supported") from exc
        except (paramiko.SSHException, ValueError):
            continue
    raise SftpAuthError("The private key could not be read (expected an OpenSSH or PEM Ed25519, RSA or ECDSA key)")


def clean_remote_path(*parts: str) -> str:
    """Join path pieces under the destination, rejecting anything that could climb out of it."""
    out: list[str] = []
    for part in parts:
        for piece in part.replace("\\", "/").split("/"):
            if piece in ("", "."):
                continue
            if piece == "..":
                raise SftpError("Remote paths may not contain '..'")
            out.append(piece)
    return "/" + "/".join(out)


class SftpSession:
    """One connection. Use as a context manager."""

    def __init__(self, target: SftpTarget, timeout: float = 15.0) -> None:
        self._t = target
        self._timeout = timeout
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def __enter__(self) -> "SftpSession":
        self.connect()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def connect(self) -> None:
        t = self._t
        if not t.host_key_sha256:
            raise SftpHostKeyError("No host key has been confirmed for this destination yet")
        transport = _open_transport(t.host, t.port, self._timeout)
        try:
            remote = fingerprint(transport.get_remote_server_key())
            if remote != t.host_key_sha256:
                raise SftpHostKeyError(
                    "The server's host key does not match the one you confirmed. "
                    "If the server was reinstalled, confirm its new key; otherwise this could be an impostor"
                )
            try:
                if t.private_key:
                    transport.auth_publickey(t.username, _load_private_key(t.private_key))
                else:
                    transport.auth_password(t.username, t.password or "")
            except paramiko.AuthenticationException as exc:
                raise SftpAuthError("The SFTP server rejected the username or credentials") from exc
            except paramiko.SSHException as exc:
                raise SftpUnreachableError("The SSH session failed during login") from exc
            self._sftp = paramiko.SFTPClient.from_transport(transport)
        except BaseException:
            transport.close()
            raise
        self._transport = transport

    def close(self) -> None:
        if self._sftp is not None:
            with contextlib.suppress(Exception):
                self._sftp.close()
        if self._transport is not None:
            with contextlib.suppress(Exception):
                self._transport.close()
        self._sftp = self._transport = None

    @property
    def sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            raise SftpError("Not connected")
        return self._sftp

    # -- operations -------------------------------------------------------------------------------------
    def makedirs(self, path: str) -> None:
        built = ""
        for piece in [p for p in path.split("/") if p]:
            built += "/" + piece
            try:
                self.sftp.stat(built)
            except FileNotFoundError:
                try:
                    self.sftp.mkdir(built)
                except OSError as exc:
                    raise SftpError(f"Cannot create the folder {built}: {exc}") from exc
            except OSError as exc:
                raise SftpError(f"Cannot access {built}: {exc}") from exc

    def size(self, path: str) -> int | None:
        try:
            size = self.sftp.stat(path).st_size
            return int(size) if size is not None else None
        except FileNotFoundError:
            return None

    def remove(self, path: str) -> None:
        with contextlib.suppress(OSError):
            self.sftp.remove(path)

    def upload(
        self,
        local: Path,
        remote: str,
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        """Upload under a temporary name, check the size on the server, then rename into place, so a half-written
        file never has the final name. Returns the verified size."""
        expected = local.stat().st_size
        self.makedirs(posixpath.dirname(remote))
        tmp = remote + ".part"
        sent = 0
        try:
            with local.open("rb") as src, self.sftp.open(tmp, "wb") as dst:
                dst.set_pipelined(True)
                while chunk := src.read(CHUNK):
                    if cancelled is not None and cancelled():
                        raise SftpCancelled("Upload cancelled")
                    dst.write(chunk)
                    sent += len(chunk)
                    if progress is not None:
                        progress(sent)
            got = self.size(tmp)
            if got != expected:
                raise SftpError(f"The server holds {got} bytes after the upload but {expected} were sent")
            try:
                self.sftp.posix_rename(tmp, remote)
            except OSError:
                self.remove(remote)
                self.sftp.rename(tmp, remote)
            final = self.size(remote)
            if final != expected:
                raise SftpError(f"After the rename the server reports {final} bytes, expected {expected}")
        except OSError as exc:
            self.remove(tmp)
            raise SftpError(f"Upload failed: {exc}") from exc
        except BaseException:
            self.remove(tmp)
            raise
        return expected

    def write_test(self, base_path: str) -> None:
        """Prove the account can create folders, write, read back and delete under the destination's base path."""
        self.makedirs(base_path)
        name = posixpath.join(base_path, f".nvrcentral-write-test-{secrets.token_hex(4)}")
        payload = secrets.token_bytes(64)
        try:
            with self.sftp.open(name, "wb") as fh:
                fh.write(payload)
            with self.sftp.open(name, "rb") as fh:
                if fh.read() != payload:
                    raise SftpError("The test file read back differently from what was written")
        except OSError as exc:
            raise SftpError(f"Cannot write under {base_path}: {exc}") from exc
        finally:
            self.remove(name)
