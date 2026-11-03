"""Own-venv metadata link exception; Linux seams do not establish native acceptance."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from oversight.workspace import IntegrityError
from oversight.workspace import metadata_io as metadata
from oversight.workspace import source_nt as nt
from oversight.workspace.metadata_io import Budget

from .test_nt_metadata import stat_fields
from .test_source_nt_env import environment
from .test_source_nt_imports import mapped_environment


@pytest.mark.parametrize("native_reader", [False, True])
@pytest.mark.parametrize("name", ["editable.pth", "own.dist-info/METADATA"])
def test_site_metadata_accepts_cache_hardlinks(tmp_path, monkeypatch, native_reader, name):
    _, _, io = environment(tmp_path)
    path = io.site / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"bounded import metadata\n")
    (tmp_path / "uv-cache-entry").hardlink_to(path)
    assert path.lstat().st_nlink == 2
    if native_reader:
        monkeypatch.setattr(metadata, "sys", SimpleNamespace(platform="win32"))
    io.check(path, directory=False)
    assert io.read(path, Budget(io.deadline)) == b"bounded import metadata\n"


@pytest.mark.parametrize("finder", [False, True])
def test_linked_editable_metadata_is_parsed_into_copy_imports(tmp_path, finder):
    source, tree, io, site = mapped_environment(tmp_path, finder=finder)
    for index, path in enumerate(
        [site / "own.pth", *(site / "different_distribution-1.dist-info").iterdir()]
        + ([site / "__editable__own_finder.py"] if finder else [])
    ):
        (tmp_path / f"cache-{index}").hardlink_to(path)
    prepared = nt.prepare_environment(source, tree, io.deadline, io=io)
    assert prepared.roots == (tree / "backend/src",)
    assert prepared.guard is not None
    assert str(source.root / "backend/src") not in prepared.guard


@pytest.mark.parametrize(
    "name",
    [".venv/pyvenv.cfg", ".venv/Scripts/python.exe", "base/python.exe", ".venv/other.pth"],
)
def test_other_environment_files_keep_typed_refusal(tmp_path, name):
    source, _, io = environment(tmp_path)
    path = tmp_path / name if name.startswith("base/") else source.root / name
    if not path.exists():
        path.write_bytes(b"metadata outside site-packages")
    (tmp_path / "uv-cache-entry").hardlink_to(path)
    assert path.lstat().st_nlink == 2
    for operation in (
        lambda: io.check(path, directory=False),
        lambda: io.read(path, Budget(io.deadline)),
    ):
        with pytest.raises(nt.HardlinkedEnvironmentError) as refused:
            operation()
        assert "uv sync --reinstall --link-mode copy" in str(refused.value)
        assert "UV_LINK_MODE=copy" in str(refused.value)
        assert str(path) not in str(refused.value)


@pytest.mark.parametrize("configured_executable", [False, True])
def test_base_interpreter_under_site_still_requires_one_link(tmp_path, configured_executable):
    source, tree, io = environment(tmp_path)
    home = io.site / "base"
    (home / "Lib").mkdir(parents=True)
    python = home / "python.exe"
    python.write_bytes(b"launcher")
    cache = tmp_path / "uv-cache-entry"
    cache.hardlink_to(python)
    io.bind = lambda raw: python if raw.endswith("python.exe") else home
    (source.root / ".venv/pyvenv.cfg").write_text(
        "home = C:/base\ninclude-system-site-packages = false\n"
        + ("executable = C:/base/python.exe\n" if configured_executable else "")
    )
    with pytest.raises(nt.HardlinkedEnvironmentError) as refused:
        nt.prepare_environment(source, tree, io.deadline, io=io)
    assert str(refused.value) == nt.HARDLINKED_ENVIRONMENT_NOTICE
    cache.unlink()
    assert nt.prepare_environment(source, tree, io.deadline, io=io).python == (
        source.root / ".venv/Scripts/python.exe"
    )


@pytest.mark.parametrize("native_reader", [False, True])
def test_shared_reader_still_refuses_hardlinks_by_default(tmp_path, monkeypatch, native_reader):
    _, _, io = environment(tmp_path)
    path = io.site / "editable.pth"
    path.write_bytes(b"metadata")
    (tmp_path / "uv-cache-entry").hardlink_to(path)
    if native_reader:
        monkeypatch.setattr(metadata, "sys", SimpleNamespace(platform="win32"))
    with pytest.raises(IntegrityError, match="nonregular or linked Git metadata"):
        metadata.read_entry(path, Budget(io.deadline))


@pytest.mark.parametrize("relative", ["../outside.pth", "../SITE-PACKAGES-other/outside.pth"])
def test_lexical_site_escape_is_refused(tmp_path, relative):
    _, _, io = environment(tmp_path)
    outside = io.site.parent / (
        "outside.pth" if relative == "../outside.pth" else "SITE-PACKAGES-other/outside.pth"
    )
    outside.parent.mkdir(exist_ok=True)
    outside.write_bytes(b"outside")
    (tmp_path / "uv-cache-entry").hardlink_to(outside)
    for path in (io.site / relative, outside):
        with pytest.raises((OSError, IntegrityError)):
            io.read(path, Budget(io.deadline))


def test_case_insensitive_site_prefix_does_not_admit_a_sibling(tmp_path):
    _, _, io = environment(tmp_path)
    outside = io.site.parent / "SITE-PACKAGES-elsewhere/own.pth"
    outside.parent.mkdir()
    outside.write_bytes(b"outside")
    (tmp_path / "uv-cache-entry").hardlink_to(outside)
    with pytest.raises(nt.HardlinkedEnvironmentError):
        io.read(outside, Budget(io.deadline))


@pytest.mark.parametrize("path_kind", ["root", "child-directory", "zero-links"])
def test_exception_requires_a_regular_file_strictly_below_site(tmp_path, monkeypatch, path_kind):
    _, _, io = environment(tmp_path)
    if path_kind == "zero-links":
        path = io.site / "editable.pth"
        path.write_bytes(b"metadata")
        fstat = os.fstat
        monkeypatch.setattr(os, "fstat", lambda fd: stat_fields(fstat(fd), st_nlink=0))
        with pytest.raises(nt.HardlinkedEnvironmentError):
            io.check(path, directory=False)
    else:
        path = io.site if path_kind == "root" else io.site / "own.dist-info"
        path.mkdir(exist_ok=True)
        monkeypatch.setattr(nt, "sys", SimpleNamespace(platform="win32"))
        lstat = Path.lstat

        def linked_directory(target, *args, **kwargs):
            info = lstat(target, *args, **kwargs)
            return stat_fields(info, st_nlink=2) if target == path else info

        monkeypatch.setattr(Path, "lstat", linked_directory)
        with pytest.raises(nt.HardlinkedEnvironmentError):
            io.check(path, directory=True)


@pytest.mark.parametrize("phase", ["open", "read", "path-recheck"])
def test_linked_native_read_still_compares_link_counts(tmp_path, monkeypatch, phase):
    path = tmp_path / "editable.pth"
    path.write_bytes(b"metadata")
    (tmp_path / "uv-cache-entry").hardlink_to(path)
    monkeypatch.setattr(metadata, "sys", SimpleNamespace(platform="win32"))
    fstat, guard = os.fstat, metadata.nt_guard
    calls = 0

    def opened(fd):
        nonlocal calls
        calls += 1
        info = fstat(fd)
        if calls == (1 if phase == "open" else 2) and phase != "path-recheck":
            return stat_fields(info, st_nlink=3)
        return info

    def guarded(target, deadline, *, working=False):
        info = guard(target, deadline, working=working)
        return stat_fields(info, st_nlink=3) if phase == "path-recheck" and calls == 2 else info

    monkeypatch.setattr(os, "fstat", opened)
    monkeypatch.setattr(metadata, "nt_guard", guarded)
    with pytest.raises(
        IntegrityError, match="changed during " + ("open" if phase == "open" else "read")
    ):
        metadata.read_entry(path, Budget(time.monotonic() + 5), allow_hardlinks=True)


def test_linked_metadata_keeps_read_budget(tmp_path):
    _, _, io = environment(tmp_path)
    path = io.site / "editable.pth"
    path.write_bytes(b"x" * (metadata.MAX_FILE + 1))
    (tmp_path / "uv-cache-entry").hardlink_to(path)
    with pytest.raises(IntegrityError, match="read limit exceeded"):
        io.read(path, Budget(io.deadline))
