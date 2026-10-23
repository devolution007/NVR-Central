"""SIMULATED SFTP server (paramiko) for automated tests. It proves our client code speaks SFTP correctly and handles
failures, it says nothing about how any real SFTP product behaves. Files live in a temp folder; password login only.
"""

import errno
import os
import socket
import threading
from pathlib import Path
from typing import Any

import paramiko
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface

from app.integrations.sftp import fingerprint

USER, PASSWORD = "backup", "sftp-secret"


class _Handle(SFTPHandle):
    def __init__(self, server: "FakeSftpServer", path: Path, flags: int) -> None:
        super().__init__(flags)
        self.server = server
        self.path = path
        mode = "r+b" if flags & os.O_RDWR else ("wb" if flags & os.O_WRONLY else "rb")
        if flags & os.O_WRONLY and not flags & os.O_TRUNC and path.exists():
            mode = "r+b"
        self.readfile = self.writefile = path.open(mode)
        self.written = 0
        self.broken = False

    def write(self, offset: int, data: bytes) -> int:
        self.written += len(data)
        limit = self.server.fail_after_bytes
        if self.broken or (limit is not None and self.written > limit):
            self.broken = True  # once a write fails the whole upload fails, like a dropped connection
            self.server.fail_after_bytes = None  # the next upload works again
            raise OSError(errno.EIO, "simulated I/O failure")
        if self.server.disk_full:
            raise OSError(errno.ENOSPC, "simulated disk full")
        return int(super().write(offset, data))  # type: ignore[no-untyped-call]


class _Sftp(SFTPServerInterface):
    def __init__(self, server: paramiko.ServerInterface, *args: Any, **kwargs: Any) -> None:
        super().__init__(server)
        self.fake: FakeSftpServer = server.fake  # type: ignore[attr-defined]

    def _p(self, path: str) -> Path:
        rel = path.lstrip("/")
        target = (self.fake.root / rel).resolve()
        if self.fake.root.resolve() not in (target, *target.parents):
            raise PermissionError
        return target

    def list_folder(self, path: str) -> Any:
        out = []
        for child in sorted(self._p(path).iterdir()):
            attr = SFTPAttributes.from_stat(child.stat())
            attr.filename = child.name
            out.append(attr)
        return out

    def stat(self, path: str) -> Any:
        try:
            return SFTPAttributes.from_stat(self._p(path).stat())
        except FileNotFoundError:
            return SFTPServer.convert_errno(errno.ENOENT)

    lstat = stat

    def open(self, path: str, flags: int, attr: Any) -> Any:
        target = self._p(path)
        if flags & os.O_WRONLY or flags & os.O_RDWR:
            if not self.fake.writable:
                return SFTPServer.convert_errno(errno.EACCES)
            if not target.parent.exists():
                return SFTPServer.convert_errno(errno.ENOENT)
            if not target.exists():
                target.touch()
        elif not target.exists():
            return SFTPServer.convert_errno(errno.ENOENT)
        return _Handle(self.fake, target, flags)

    def remove(self, path: str) -> int:
        try:
            self._p(path).unlink()
        except FileNotFoundError:
            return SFTPServer.convert_errno(errno.ENOENT)
        return paramiko.SFTP_OK

    def rename(self, oldpath: str, newpath: str) -> int:
        dest = self._p(newpath)
        if dest.exists():
            return SFTPServer.convert_errno(errno.EEXIST)
        self._p(oldpath).rename(dest)
        return paramiko.SFTP_OK

    def posix_rename(self, oldpath: str, newpath: str) -> int:
        self._p(oldpath).replace(self._p(newpath))
        return paramiko.SFTP_OK

    def mkdir(self, path: str, attr: Any) -> int:
        if not self.fake.writable:
            return SFTPServer.convert_errno(errno.EACCES)
        try:
            self._p(path).mkdir()
        except FileExistsError:
            return SFTPServer.convert_errno(errno.EEXIST)
        except FileNotFoundError:
            return SFTPServer.convert_errno(errno.ENOENT)
        return paramiko.SFTP_OK

    def canonicalize(self, path: str) -> str:
        return "/" + path.strip("/") if path.strip("/") else "/"


class _Auth(paramiko.ServerInterface):
    def __init__(self, fake: "FakeSftpServer") -> None:
        self.fake = fake

    def check_auth_password(self, username: str, password: str) -> int:
        ok = username == USER and password == PASSWORD and not self.fake.reject_login
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class FakeSftpServer:
    def __init__(self, root: Path, port: int = 0) -> None:
        self.root = root
        self.host_key = paramiko.RSAKey.generate(2048)
        self.fingerprint = fingerprint(self.host_key)
        self.fail_after_bytes: int | None = None
        self.disk_full = False
        self.writable = True
        self.reject_login = False
        self.accepting = True
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(8)
        self.port: int = self._sock.getsockname()[1]
        self._stop = False
        self._transports: list[paramiko.Transport] = []

    def start(self) -> "FakeSftpServer":
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop = True
        self._sock.close()
        for t in self._transports:
            t.close()

    def drop_connections(self) -> None:
        """Simulate the network dying under an open session."""
        for t in self._transports:
            t.close()

    def _accept(self) -> None:
        while not self._stop:
            try:
                client, _ = self._sock.accept()
            except OSError:
                return
            if not self.accepting:
                client.close()
                continue
            # One thread per connection: a client that hangs up mid-handshake (e.g. after seeing a wrong host key)
            # must not block the next client.
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        t = paramiko.Transport(client)
        t.add_server_key(self.host_key)
        t.set_subsystem_handler("sftp", SFTPServer, _Sftp)
        self._transports.append(t)
        try:
            t.start_server(server=_Auth(self))
        except (paramiko.SSHException, EOFError, OSError):
            t.close()
