"""Native stat semantics at the shared metadata boundary, injected on Linux."""

import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from oversight.workspace import IntegrityError
from oversight.workspace import metadata_io as metadata
from oversight.workspace.metadata_io import Budget, Entry


def token_stat(
    *, mode=stat.S_IFREG | 0o666, ino=42, dev=7, nlink=1, size=5, mtime_ns=123456789, ctime_ns=123
):
    return os.stat_result(
        (mode, ino, dev, nlink, 0, 0, size, 0, 0, 0),
        {"st_mtime_ns": mtime_ns, "st_ctime_ns": ctime_ns, "st_birthtime_ns": ctime_ns},
    )


@pytest.mark.parametrize("execute", [0o001, 0o010, 0o100, 0o111])
def test_nt_token_ignores_name_derived_execute_bits(execute):
    before = token_stat()
    after = token_stat(mode=before.st_mode | execute)
    assert metadata.nt_token(before) == metadata.nt_token(after)
    assert metadata.nt_token(before) == (7, 42, stat.S_IFREG | 0o666, 1, 5, 123456789, 123)


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": stat.S_IFDIR | 0o666},
        {"mode": stat.S_IFREG | 0o444},
        {"ino": 43},
        {"dev": 8},
        {"nlink": 2},
        {"size": 6},
        {"mtime_ns": 123456790},
        {"ctime_ns": 124},
    ],
    ids=["type", "readonly", "inode", "device", "links", "size", "mtime-ns", "creation-ns"],
)
def test_nt_token_keeps_other_identity_fields(changes):
    assert metadata.nt_token(token_stat()) != metadata.nt_token(token_stat(**changes))


def stat_fields(info, **changes):
    fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
    return SimpleNamespace(**(fields | changes))


def inject_name_execute_bits(monkeypatch, path):
    lstat = Path.lstat

    def named_stat(target, *args, **kwargs):
        info = lstat(target, *args, **kwargs)
        return stat_fields(info, st_mode=info.st_mode | 0o111) if target == path else info

    monkeypatch.setattr(Path, "lstat", named_stat)


def test_environment_check_accepts_name_derived_execute_bits(tmp_path, monkeypatch):
    from .test_source_nt_env import environment

    source, _, io = environment(tmp_path)
    path = source.root / ".venv/Scripts/python.exe"
    inject_name_execute_bits(monkeypatch, path)
    io.check(path, directory=False)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"st_nlink": 2}, "environment file is hard-linked"),
        ({"st_nlink": 1, "st_size": -1}, "unsafe environment file$"),
    ],
    ids=["hardlink", "token-mismatch"],
)
def test_environment_check_refuses_unsafe_handle(tmp_path, monkeypatch, changes, reason):
    from .test_source_nt_env import environment

    source, _, io = environment(tmp_path)
    path = source.root / ".venv/Scripts/python.exe"
    fstat = os.fstat
    monkeypatch.setattr(os, "fstat", lambda fd: stat_fields(fstat(fd), **changes))
    with pytest.raises(OSError, match="^Windows source refused: " + reason) as refused:
        io.check(path, directory=False)
    if changes["st_nlink"] == 2:
        assert "--link-mode copy" in str(refused.value)
        assert "UV_LINK_MODE=copy" in str(refused.value)
        assert str(path) not in str(refused.value)


@pytest.mark.parametrize("kind", ["Git metadata", "working file"])
def test_native_read_accepts_name_derived_execute_bits(tmp_path, monkeypatch, kind):
    path = tmp_path / "make.bat"
    path.write_bytes(b"stable\r\n\x1a")
    inject_name_execute_bits(monkeypatch, path)
    entry = metadata.read_at(tmp_path, path.name, path, Budget(time.monotonic() + 5), kind=kind)
    assert entry is not None and entry.data == path.read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative mode comparison")
def test_posix_read_still_compares_execute_bits(tmp_path, monkeypatch):
    path = tmp_path / "input"
    path.write_bytes(b"stable")
    fstat = os.fstat

    def opened(fd):
        info = fstat(fd)
        return stat_fields(info, st_mode=info.st_mode ^ 0o111)

    monkeypatch.setattr(os, "fstat", opened)
    deadline = time.monotonic() + 5
    with metadata.directory(tmp_path, deadline) as fd:
        assert isinstance(fd, int)
        with pytest.raises(IntegrityError, match="changed during open"):
            metadata.read_at(fd, path.name, path, Budget(deadline))


def inject_native_times(monkeypatch, path, *, change=None):
    """3.12 lstat reports creation as ctime; fstat reports NTFS ChangeTime."""
    monkeypatch.setattr(metadata, "sys", SimpleNamespace(platform="win32"))
    guard, fstat = metadata.nt_guard, os.fstat
    original = path.lstat()
    birth = original.st_ctime_ns
    calls = 0

    def guarded(target, deadline, *, working=False):
        info = guard(target, deadline, working=working)
        return (
            stat_fields(info, st_birthtime_ns=birth, st_ctime_ns=birth) if target == path else info
        )

    def opened(fd):
        nonlocal calls
        info = fstat(fd)
        if (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino):
            return info
        calls += 1
        info = stat_fields(info, st_birthtime_ns=birth, st_ctime_ns=birth + 100)
        if change is not None:
            info = change(info, calls)
        return info

    monkeypatch.setattr(metadata, "nt_guard", guarded)
    monkeypatch.setattr(os, "fstat", opened)


@pytest.mark.parametrize("kind", ["Git metadata", "working file"])
def test_native_creation_and_change_times_can_differ(tmp_path, monkeypatch, kind):
    path = tmp_path / "input"
    path.write_bytes(b"stable\r\n\x1a")
    inject_native_times(monkeypatch, path)
    entry = metadata.read_at(tmp_path, path.name, path, Budget(time.monotonic() + 5), kind=kind)
    assert entry is not None and entry.data == path.read_bytes()


@pytest.mark.parametrize(
    "field,value",
    [
        ("st_dev", -1),
        ("st_ino", -1),
        ("st_mode", stat.S_IFDIR | 0o755),
        ("st_nlink", 2),
        ("st_size", 999),
        ("st_mtime_ns", -1),
        ("st_birthtime_ns", -1),
        ("st_ctime_ns", -1),
    ],
)
def test_native_read_still_refuses_handle_changes(tmp_path, monkeypatch, field, value):
    path = tmp_path / "input"
    path.write_bytes(b"stable")

    def change(info, calls):
        return stat_fields(info, **{field: value}) if calls == 2 else info

    inject_native_times(monkeypatch, path, change=change)
    with pytest.raises(IntegrityError, match="changed during read"):
        metadata.read_at(tmp_path, path.name, path, Budget(time.monotonic() + 5))


@pytest.mark.parametrize("name", ["HEAD", "index"])
def test_source_metadata_accepts_native_timestamp_semantics(tmp_path, monkeypatch, name):
    from oversight.workspace.source_nt import source_metadata

    (tmp_path / ".git").mkdir()
    path = tmp_path / ".git" / name
    path.write_bytes(b"source metadata")
    inject_native_times(monkeypatch, path)
    observed = {key: digest for key, _, digest in source_metadata(tmp_path, time.monotonic() + 5)}
    assert observed[name] is not None


@pytest.mark.anyio
async def test_native_snapshot_keeps_git_authority(workspace, monkeypatch):
    from oversight.workspace import snapshot

    inject_native_times(monkeypatch, workspace.main.git_identity.common / "config")
    monkeypatch.setattr(snapshot, "sys", SimpleNamespace(platform="win32"))
    saved = snapshot.take_snapshot(workspace.main.git_identity, workspace.integrity, 5)
    snapshot.finish_snapshot(saved, workspace.integrity)
    assert not workspace.integrity.main_suspect and not workspace.integrity.rules_suspended


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["validate", "copy"])
@pytest.mark.parametrize("mutated", [False, True])
async def test_source_reads_keep_handle_change_time(tmp_path, monkeypatch, phase, mutated):
    from oversight.workspace import source_nt as source

    from .test_source_nt import Git, collect, source_case, unnamed

    with source_case(tmp_path) as (selected, fd):
        path = selected.root / "a.py"
        path.write_bytes(b"x = 1\n")
        listing = await collect(selected, fd, Git())
        selection = source.validate_selection(listing, sensitive=lambda _: False, streams=unnamed)

        def change(info, calls):
            return (
                stat_fields(info, st_ctime_ns=info.st_ctime_ns + 1)
                if calls == 2 and mutated
                else info
            )

        inject_native_times(monkeypatch, path, change=change)

        def read():
            if phase == "validate":
                return source.validate_selection(
                    listing, sensitive=lambda _: False, streams=unnamed
                )
            stage = source.StagedSource(
                selection, selected.runtime / "stage", selected.runtime / "manifest"
            )
            return source.copy_selection(stage, time.monotonic() + 5, time.monotonic)

        if mutated:
            with pytest.raises(OSError, match="changed during"):
                read()
        else:
            read()


@pytest.mark.parametrize("readonly", [False, True])
def test_native_cas_compares_windows_permissions(tmp_path, monkeypatch, readonly):
    path = tmp_path / "index"
    path.write_bytes(b"metadata")
    expected = Entry(0o400 if readonly else 0o600, b"metadata")
    actual = Entry(0o444 if readonly else 0o666, b"metadata")
    monkeypatch.setattr(metadata, "read_at", lambda *a, **k: actual)
    metadata._same(tmp_path, path, expected, time.monotonic() + 5)


@pytest.mark.parametrize("mutation", ["bytes", "readonly", "missing"])
def test_native_cas_still_refuses_real_changes(tmp_path, monkeypatch, mutation):
    path = tmp_path / "index"
    expected = Entry(0o600, b"metadata")
    actual = {
        "bytes": Entry(0o666, b"modified"),
        "readonly": Entry(0o444, b"metadata"),
        "missing": None,
    }[mutation]
    monkeypatch.setattr(metadata, "read_at", lambda *a, **k: actual)
    with pytest.raises(IntegrityError, match="compare-and-swap failed"):
        metadata._same(tmp_path, path, expected, time.monotonic() + 5)


def test_posix_cas_still_compares_every_permission_bit(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata, "read_at", lambda *a, **k: Entry(0o666, b"metadata"))
    with pytest.raises(IntegrityError, match="compare-and-swap failed"):
        metadata._same(123, tmp_path / "index", Entry(0o600, b"metadata"), time.monotonic() + 5)
