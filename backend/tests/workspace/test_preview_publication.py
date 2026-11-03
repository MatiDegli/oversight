"""Hostile build outputs never become an unchecked serving path."""

import asyncio
import os
import sys
from dataclasses import FrozenInstanceError

import pytest

from oversight.workspace.preview_publication import OutputBudgets, publish_build, request_path

pytestmark = pytest.mark.anyio


async def test_publication_pinned_handles_and_swaps(tmp_path):
    source = tmp_path / "tree"
    source.mkdir()
    (source / "index.html").write_bytes(b"verified")
    build = await publish_build(source, tmp_path / "published")
    try:
        (source / "index.html").write_bytes(b"changed")
        assert build.read("index.html") == b"verified"
        assert build.total_bytes == 8
        with pytest.raises(FrozenInstanceError):
            field_name = "total_bytes"
            setattr(build, field_name, 0)
    finally:
        build.close()
    with pytest.raises(ValueError):
        build.read("index.html")
    assert not (tmp_path / "published").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "/../secret",
        "/%2e%2e/secret",
        "/a%2fb",
        "/a%5cb",
        "/%252e%252e/x",
        "//host/x",
        "http://host/x",
        "/C:/x",
        "/a\\b",
        "/a%00b",
        "/con.txt",
        "/a./x",
        "/a /x",
        "/a:b",
        "/x//y",
        "/x/./y",
        "/%zz",
        "/a%0ab",
    ],
)
async def test_publication_path_and_link_attacks_encoding(raw):
    with pytest.raises(ValueError):
        request_path(raw)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory_link", "fifo"])
async def test_publication_path_and_link_attacks(tmp_path, kind):
    source = tmp_path / "tree"
    source.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("secret")
    target = source / "index.html"
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, target)
    elif kind == "directory_link":
        target.symlink_to(tmp_path, target_is_directory=True)
    else:
        if sys.platform == "win32":
            pytest.skip("POSIX FIFO requires POSIX")
        os.mkfifo(target)
    with pytest.raises((OSError, ValueError)):
        await publish_build(source, tmp_path / "published")
    assert not (tmp_path / "published").exists()


@pytest.mark.parametrize(
    "file_limit,total_limit,count,ok",
    [
        (4, 8, 2, True),
        (3, 8, 2, False),
        (4, 7, 2, False),
        (4, 8, 1, False),
        (5, 9, 3, True),
        (0, 8, 2, False),
    ],
)
async def test_publication_output_budgets(tmp_path, file_limit, total_limit, count, ok):
    source = tmp_path / "tree"
    source.mkdir()
    for name in ("index.html", "worker.js"):
        (source / name).write_bytes(b"1234")
    budgets = OutputBudgets(file_limit, total_limit, count)
    if not ok:
        with pytest.raises(ValueError):
            await publish_build(source, tmp_path / "published", budgets=budgets)
    else:
        build = await publish_build(source, tmp_path / "published", budgets=budgets)
        build.close()
    assert not (tmp_path / "published").exists()


async def test_publication_cancel_and_partial_setup(tmp_path):
    source = tmp_path / "tree"
    source.mkdir()
    (source / "index.html").write_bytes(b"x" * 1024)
    with pytest.raises(ValueError):
        await publish_build(source, source / "published")
    task = asyncio.create_task(publish_build(source, tmp_path / "published"))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / "published").exists()


@pytest.mark.parametrize("attack", ["replace_file", "replace_directory", "in_place", "root"])
async def test_publication_pinned_handles_and_swaps_during_copy(tmp_path, monkeypatch, attack):
    from oversight.workspace import preview_publication as publication

    source = tmp_path / "tree"
    source.mkdir()
    (source / "assets").mkdir()
    target = source / "assets" / "app.js"
    target.write_bytes(b"original")
    original = publication._copy
    attacked = False

    def racing_copy(item, output, deadline):
        nonlocal attacked
        original(item, output, deadline)
        if attacked:
            return
        attacked = True
        if attack == "replace_file":
            target.rename(target.with_suffix(".old"))
            target.write_bytes(b"attacker")
        elif attack == "replace_directory":
            (source / "assets").rename(source / "old")
            (source / "assets").mkdir()
            (source / "assets" / "app.js").write_bytes(b"attacker")
        elif attack == "in_place":
            target.write_bytes(b"attacker")
        elif attack == "root":
            source.rename(tmp_path / "old")
            source.mkdir()

    monkeypatch.setattr(publication, "_copy", racing_copy)
    with pytest.raises((OSError, ValueError)):
        await publication.publish_build(source, tmp_path / "published")
    assert not (tmp_path / "published").exists()


async def test_publication_path_and_link_attacks_cross_mount():
    import errno
    import sys

    from oversight.workspace import preview_publication as publication

    if sys.platform != "linux":
        pytest.skip("Linux openat2 mount traversal requires Linux")
    root = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError) as denied:
            publication._linux_open(root, "proc/version")
        assert denied.value.errno == errno.EXDEV
    finally:
        os.close(root)


async def test_publication_output_budgets_entries_empty_and_deadline(tmp_path):
    source = tmp_path / "tree"
    source.mkdir()
    for n in range(3):
        (source / str(n)).mkdir()
    with pytest.raises(ValueError):
        await publish_build(source, tmp_path / "published", budgets=OutputBudgets(entries=3))
    build = await publish_build(
        source, tmp_path / "published", budgets=OutputBudgets(files=0, entries=4)
    )
    assert build.paths == () and build.total_bytes == 0
    build.close()
    with pytest.raises(TimeoutError):
        await publish_build(source, tmp_path / "published", budgets=OutputBudgets(seconds=1e-12))
    assert not (tmp_path / "published").exists()


async def test_publication_cancel_during_copy_closes_handles(tmp_path, monkeypatch):
    import threading

    from oversight.workspace import preview_publication as publication

    source = tmp_path / "tree"
    source.mkdir()
    (source / "index.html").write_text("data")
    entered, release = threading.Event(), threading.Event()
    original = publication._copy

    def waiting_copy(item, output, deadline):
        entered.set()
        assert release.wait(2)
        original(item, output, deadline)

    monkeypatch.setattr(publication, "_copy", waiting_copy)
    task = asyncio.create_task(publish_build(source, tmp_path / "published"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / "published").exists()


async def test_publication_requires_openat2_and_no_follow_root(tmp_path, monkeypatch):
    import errno

    from oversight.workspace import preview_publication as publication

    source = tmp_path / "tree"
    source.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(source, target_is_directory=True)
    with pytest.raises(OSError):
        await publish_build(linked, tmp_path / "published")

    def unavailable(*args, **kwargs):
        raise OSError(errno.ENOSYS, "openat2 unavailable")

    monkeypatch.setattr(publication, "_linux_open", unavailable)
    with pytest.raises(OSError, match="openat2 unavailable"):
        await publish_build(source, tmp_path / "published")
    assert not (tmp_path / "published").exists()


async def test_publication_verifies_copied_bytes(tmp_path):
    import io
    import time

    from oversight.workspace import preview_publication as publication

    class CorruptOutput(io.BytesIO):
        def write(self, data):
            return super().write(bytes(byte ^ 1 for byte in data))

    path = tmp_path / "input"
    path.write_bytes(b"original")
    fd = os.open(path, os.O_RDONLY)
    try:
        item = publication._Pinned(
            fd, -1, "input", "input", path, publication._identity(os.fstat(fd)), None
        )
        with pytest.raises(ValueError, match="byte verification failed"):
            publication._copy(item, CorruptOutput(), time.monotonic() + 2)
    finally:
        os.close(fd)


async def test_publication_concurrent_swap_retains_original_handle(tmp_path, monkeypatch):
    import threading

    from oversight.workspace import preview_publication as publication

    source = tmp_path / "tree"
    source.mkdir()
    target = source / "index.html"
    target.write_bytes(b"original")
    outside = tmp_path / "secret"
    outside.write_bytes(b"must not publish")
    pinned, swapped = threading.Event(), threading.Event()
    original = publication._copy
    copied = []

    def copying(item, output, deadline):
        pinned.set()
        assert swapped.wait(2)
        original(item, output, deadline)
        output.seek(0)
        copied.append(output.read())

    def attack():
        assert pinned.wait(2)
        target.rename(source / "old")
        target.symlink_to(outside)
        swapped.set()

    monkeypatch.setattr(publication, "_copy", copying)
    actor = asyncio.create_task(asyncio.to_thread(attack))
    try:
        with pytest.raises((OSError, ValueError)):
            await publish_build(source, tmp_path / "published")
    finally:
        await actor
    assert copied == [b"original"]
    assert not (tmp_path / "published").exists()


async def test_publication_windows_reparse_components(tmp_path):
    import ctypes
    import struct
    import sys

    if sys.platform != "win32":
        pytest.skip("unproven: native Windows reparse/junction checks require Windows")
    source = tmp_path / "tree"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret")
    (outside / "build").mkdir()
    (outside / "build" / "index.html").write_text("secret")
    (source / "index.html").write_text("ordinary")
    verified = await publish_build(source, tmp_path / "published")
    assert verified.read("index.html") == b"ordinary"
    verified.close()
    junction = source / "junction"
    junction.mkdir()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.DeviceIoControl.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
    ]
    handle = kernel.CreateFileW(str(junction), 0x40000000, 0, None, 3, 0x02200000, None)
    assert handle not in {None, ctypes.c_void_p(-1).value}, ctypes.get_last_error()
    substitute = ("\\??\\" + str(outside)).encode("utf-16-le")
    display = str(outside).encode("utf-16-le")
    names = substitute + b"\0\0" + display + b"\0\0"
    data = (
        struct.pack(
            "<IHHHHHH",
            0xA0000003,
            8 + len(names),
            0,
            0,
            len(substitute),
            len(substitute) + 2,
            len(display),
        )
        + names
    )
    returned = ctypes.c_ulong()
    buffer = ctypes.create_string_buffer(data)
    try:
        assert kernel.DeviceIoControl(
            handle, 0x900A4, buffer, len(data), None, 0, ctypes.byref(returned), None
        ), ctypes.get_last_error()
    finally:
        kernel.CloseHandle(handle)
    try:
        with pytest.raises((OSError, ValueError)):
            await publish_build(source, tmp_path / "published")
        with pytest.raises((OSError, ValueError)):
            await publish_build(junction, tmp_path / "published")
        with pytest.raises((OSError, ValueError)):
            await publish_build(junction / "build", tmp_path / "published")
        assert not (tmp_path / "published").exists()
    finally:
        junction.rmdir()


async def test_publication_zero_and_frozen_budget_boundaries(tmp_path):
    assert (OutputBudgets().file_bytes, OutputBudgets().total_bytes, OutputBudgets().files) == (
        16 * 1024 * 1024,
        256 * 1024 * 1024,
        10_000,
    )
    source = tmp_path / "tree"
    source.mkdir()
    with pytest.raises(ValueError):
        await publish_build(source, tmp_path / "published", budgets=OutputBudgets(entries=0))
    (source / "empty.js").touch()
    limits = OutputBudgets(file_bytes=0, total_bytes=0, files=1, entries=2)
    with pytest.raises(FrozenInstanceError):
        key = "file_bytes"
        setattr(limits, key, 100)
    build = await publish_build(source, tmp_path / "published", budgets=limits)
    try:
        assert build.read("empty.js") == b"" and build.total_bytes == 0
    finally:
        build.close()


async def test_publication_copy_failure_releases_every_descriptor(tmp_path, monkeypatch):
    import errno

    from oversight.workspace import preview_publication as publication

    source = tmp_path / "tree"
    source.mkdir()
    (source / "index.html").write_text("data")
    opened = []
    original = publication._open

    def track(parent, name):
        fd = original(parent, name)
        opened.append(fd)
        return fd

    def fail(*args):
        raise OSError("injected copy failure")

    monkeypatch.setattr(publication, "_open", track)
    monkeypatch.setattr(publication, "_copy", fail)
    with pytest.raises(OSError, match="injected copy failure"):
        await publish_build(source, tmp_path / "published")
    for fd in opened:
        with pytest.raises(OSError) as error:
            os.fstat(fd)
        assert error.value.errno == errno.EBADF
    assert not (tmp_path / "published").exists()


@pytest.mark.parametrize("limit,allowed", [(2, False), (3, True), (4, True)])
async def test_publication_path_metadata_budget(tmp_path, limit, allowed):
    source = tmp_path / "tree"
    (source / "a").mkdir(parents=True)
    (source / "a" / "b").write_text("file")
    if allowed:
        build = await publish_build(
            source, tmp_path / "published", budgets=OutputBudgets(path_bytes=limit)
        )
        assert build.read("a/b") == b"file"
        build.close()
    else:
        with pytest.raises(ValueError, match="path budget exceeded"):
            await publish_build(
                source, tmp_path / "published", budgets=OutputBudgets(path_bytes=limit)
            )
    assert not (tmp_path / "published").exists()
