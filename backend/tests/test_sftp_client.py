from collections.abc import Iterator
from pathlib import Path

import pytest

from app.integrations.sftp import (
    SftpAuthError,
    SftpCancelled,
    SftpError,
    SftpHostKeyError,
    SftpSession,
    SftpTarget,
    SftpUnreachableError,
    clean_remote_path,
    fetch_host_key,
)
from tests.support.fake_sftp import PASSWORD, USER, FakeSftpServer


@pytest.fixture
def server(tmp_path: Path) -> Iterator[FakeSftpServer]:
    root = tmp_path / "remote"
    root.mkdir()
    s = FakeSftpServer(root).start()
    yield s
    s.stop()


def target(s: FakeSftpServer, **over: object) -> SftpTarget:
    args: dict[str, object] = {
        "host": "127.0.0.1", "port": s.port, "username": USER, "password": PASSWORD, "base_path": "/backups",
        "host_key_sha256": s.fingerprint,
    }  # fmt: skip
    args.update(over)
    return SftpTarget(**args)  # type: ignore[arg-type]


def test_fetch_host_key_returns_the_fingerprint_without_logging_in(server: FakeSftpServer) -> None:
    info = fetch_host_key("127.0.0.1", server.port)
    assert info.sha256 == server.fingerprint and info.sha256.startswith("SHA256:") and "ssh-rsa" in info.key_type


def test_write_test_creates_and_cleans_up(server: FakeSftpServer) -> None:
    with SftpSession(target(server)) as s:
        s.write_test("/backups/campus-a")
    assert (server.root / "backups" / "campus-a").is_dir()
    assert list((server.root / "backups" / "campus-a").iterdir()) == []


def test_refuses_a_server_whose_key_is_not_the_pinned_one(server: FakeSftpServer) -> None:
    with pytest.raises(SftpHostKeyError, match="does not match"):
        SftpSession(target(server, host_key_sha256="SHA256:AAAA")).connect()


def test_refuses_to_connect_when_no_key_was_ever_confirmed(server: FakeSftpServer) -> None:
    with pytest.raises(SftpHostKeyError, match="No host key"):
        SftpSession(target(server, host_key_sha256=None)).connect()


def test_wrong_password_is_an_auth_error_that_does_not_echo_it(server: FakeSftpServer) -> None:
    with pytest.raises(SftpAuthError) as e:
        SftpSession(target(server, password="nope-nope")).connect()
    assert "nope-nope" not in e.value.message


def test_unreachable_server(server: FakeSftpServer) -> None:
    port = server.port
    server.stop()
    with pytest.raises(SftpUnreachableError):
        SftpSession(target(server, port=port), timeout=2).connect()


def test_upload_verifies_size_and_leaves_no_part_file(server: FakeSftpServer, tmp_path: Path) -> None:
    src = tmp_path / "a.dav"
    src.write_bytes(b"x" * 700_000)
    seen: list[int] = []
    with SftpSession(target(server)) as s:
        assert s.upload(src, "/backups/c/n/cam/2026/10/23/a.dav", seen.append) == 700_000
    dest = server.root / "backups/c/n/cam/2026/10/23"
    assert [p.name for p in dest.iterdir()] == ["a.dav"] and (dest / "a.dav").read_bytes() == src.read_bytes()
    assert seen[-1] == 700_000


def test_upload_overwrites_an_older_remote_file_of_the_same_name(server: FakeSftpServer, tmp_path: Path) -> None:
    src = tmp_path / "a.dav"
    src.write_bytes(b"new-content")
    (server.root / "backups").mkdir()
    (server.root / "backups" / "a.dav").write_bytes(b"old")
    with SftpSession(target(server)) as s:
        s.upload(src, "/backups/a.dav")
    assert (server.root / "backups" / "a.dav").read_bytes() == b"new-content"


def test_failed_upload_removes_the_temporary_file(server: FakeSftpServer, tmp_path: Path) -> None:
    src = tmp_path / "a.dav"
    src.write_bytes(b"x" * 900_000)
    server.fail_after_bytes = 300_000
    with SftpSession(target(server)) as s, pytest.raises(SftpError):
        s.upload(src, "/backups/a.dav")
    assert not list((server.root / "backups").glob("a.dav*"))


def test_cancel_stops_the_upload_and_cleans_up(server: FakeSftpServer, tmp_path: Path) -> None:
    src = tmp_path / "a.dav"
    src.write_bytes(b"x" * 900_000)
    calls = iter([False, False, True])
    with SftpSession(target(server)) as s, pytest.raises(SftpCancelled):
        s.upload(src, "/backups/a.dav", cancelled=lambda: next(calls, True))
    assert not list((server.root / "backups").glob("a.dav*"))


def test_write_test_reports_a_read_only_account(server: FakeSftpServer) -> None:
    server.writable = False
    with SftpSession(target(server, base_path="/")) as s, pytest.raises(SftpError):
        s.write_test("/readonly")


def test_remote_paths_cannot_escape_the_destination() -> None:
    assert clean_remote_path("/backups/", "a b", "x//y/./z.dav") == "/backups/a b/x/y/z.dav"
    with pytest.raises(SftpError):
        clean_remote_path("/backups", "../etc")
