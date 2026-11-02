"""Placement checks run on Linux without resolving a Windows input."""

import ctypes
import os
import stat
import sys
from collections.abc import Generator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from oversight.workspace._platform import (
    Kernel,
    _StreamData,
    case_sensitive_directory,
    preflight_placement,
    stream_names,
)
from oversight.workspace.errors import InvalidPath
from oversight.workspace.source import BOLTONS
from oversight.workspace.source_nt import overlaps, prepare_source, validate_selection

from .test_source_nt import listing, source_case


@pytest.mark.parametrize(
    "path",
    [
        r"\\host\share\x",
        r"\\?\C:\x",
        r"\\.\C:\x",
        r"C:x",
        r"x",
        r"C:/a/../b",
        r"C:/a:s",
        r"C:/GIT~1",
        r"C:/a.",
        r"C:/a ",
        r"C:/CON",
        r"C:/a//b",
    ],
)
def test_lexical_before_any_io(path: str) -> None:
    def forbidden(_: str) -> None:
        pytest.fail("early refusal performed I/O")

    with pytest.raises((OSError, InvalidPath)):
        prepare_source(
            "C:/source",
            "C:/runtime",
            "C:/ide",
            path,
            preflight=forbidden,
            bind=lambda _: pytest.fail("bind"),
        )


@pytest.mark.parametrize(
    "source,runtime,allowed",
    [
        ("C:/s", "C:/s/r", False),
        ("C:/r/s", "C:/r", False),
        ("C:/r", "C:/r", False),
        ("D:/s", "C:/r", True),
        ("C:/r/sources/boltons-d31d821", "C:/r", True),
        ("C:/r/workspace", "C:/r", False),
    ],
)
def test_overlap_profile_exception(source: str, runtime: str, allowed: bool) -> None:
    from oversight.workspace.source_nt import absolute_drive

    if allowed:
        overlaps(absolute_drive(source), absolute_drive(runtime), profile=BOLTONS)
    else:
        with pytest.raises(OSError, match="separate clone"):
            overlaps(absolute_drive(source), absolute_drive(runtime), profile=BOLTONS)


@pytest.mark.parametrize(
    "volume",
    [
        (4, "NTFS", r"\Device\HarddiskVolume1"),
        (3, "ReFS", r"\Device\HarddiskVolume1"),
        (3, "NTFS", r"\??\C:\elsewhere"),
        (3, "NTFS", r"\Device\LanmanRedirector\share"),
        (2, "NTFS", ""),
    ],
)
def test_volumes(volume: tuple[int, str, str]) -> None:
    with pytest.raises(OSError, match="fixed local NTFS"):
        preflight_placement("C:/private/source", nt=True, query=lambda _: volume, synced=[])


def test_sync_roots_and_outside_profile_warning(caplog: pytest.LogCaptureFixture) -> None:
    def missing(_: str) -> os.stat_result:
        raise FileNotFoundError

    preflight = partial(
        preflight_placement,
        nt=True,
        query=lambda _: (3, "NTFS", r"\Device\HarddiskVolume1"),
        lstat=missing,
        synced=["C:/cloud"],
        userprofile="C:/Users/person",
    )
    with pytest.raises(OSError, match="nonsynced"):
        preflight("C:/cloud/source")
    paths: list[str] = []
    result = prepare_source(
        "C:/source",
        "C:/runtime",
        "C:/ide",
        "C:/source/.venv",
        preflight=preflight,
        bind=lambda p: paths.append(p) or Path(p),
    )
    assert len(paths) == (6 if sys.platform == "win32" else 2)
    assert result.source_path == r"C:\source"
    assert "outside USERPROFILE" in caplog.text


@pytest.mark.parametrize("attributes", [0x400, 0x1000, 0x40000, 0x400000])
def test_ancestor_evidence(attributes: int) -> None:
    info = cast(
        os.stat_result, SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=attributes)
    )
    with pytest.raises(OSError, match="reparse, offline or recall"):
        preflight_placement(
            "C:/source",
            nt=True,
            query=lambda _: (3, "NTFS", r"\Device\HarddiskVolume1"),
            synced=[],
            lstat=lambda _: info,
        )


@pytest.mark.parametrize(
    "attack",
    [
        "hardlink",
        "symlink",
        "special",
        "junction",
        "casefold",
        "uppercase",
        "sensitive",
        "stream",
        "alias",
        "changed",
    ],
)
def test_selected_entries(tmp_path: Path, attack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with source_case(tmp_path) as (source, _):
        target = source.root / ("ALIAS" if attack == "alias" else "a.py")
        target.write_bytes(b"original\r\n")
        if attack == "hardlink":
            os.link(target, source.root / "other")
        elif attack in {"symlink", "special", "junction"}:
            original = os.lstat
            mode = {"symlink": stat.S_IFLNK, "special": stat.S_IFIFO, "junction": stat.S_IFDIR}[
                attack
            ]
            fake = cast(
                os.stat_result,
                SimpleNamespace(
                    st_mode=mode, st_file_attributes=0x400 if attack == "junction" else 0
                ),
            )
            monkeypatch.setattr(
                os,
                "lstat",
                lambda p: fake if Path(p) == target else original(p),
            )
        elif attack in {"casefold", "uppercase"}:
            original_iter = Path.iterdir
            names = ("X", "x") if attack == "casefold" else ("i", "\u0131")
            monkeypatch.setattr(
                Path,
                "iterdir",
                lambda p: iter(p / n for n in names) if p == source.root else original_iter(p),
            )
        if attack == "alias":
            original_scan = os.scandir

            @contextmanager
            def scan(path: Path):
                with original_scan(path) as children:
                    yield [
                        SimpleNamespace(name=".ENV") if e.name == "ALIAS" else e for e in children
                    ]

            monkeypatch.setattr(os, "scandir", scan)
            monkeypatch.setattr(
                "oversight.workspace.source_nt.open_nt", lambda p: pytest.fail("alias content read")
            )
        elif attack == "changed":

            def opened(path: Path):
                handle = path.open("rb")
                path.write_bytes(b"changed")
                return handle

            monkeypatch.setattr("oversight.workspace.source_nt.open_nt", opened)

        def streams(path: Path) -> Generator[str]:
            yield "::$DATA"
            if path == target and attack == "stream":
                yield ":Zone.Identifier:$DATA"

        with pytest.raises(
            (OSError, InvalidPath),
            match=r"linked|regular|collision|sensitive|Zone.Identifier|changed|alias",
        ):
            validate_selection(
                listing(source, (target.name,)),
                sensitive=lambda _: attack == "sensitive",
                streams=streams,
                clock=lambda: 0,
            )


@pytest.mark.parametrize("fault", ["ok", "first", "next", "case", "close"])
def test_raw_stream_and_case_handles(tmp_path: Path, fault: str) -> None:
    closed: list[int] = []

    def first(path: str, level: int, data: Any, flags: int) -> int:
        ctypes.cast(data, ctypes.POINTER(_StreamData)).contents.name = "::$DATA"
        return int(ctypes.c_void_p(-1).value or 0) if fault == "first" else 42

    calls = 0

    def next_stream(handle: int, data: Any) -> int:
        nonlocal calls
        calls += 1
        ctypes.cast(data, ctypes.POINTER(_StreamData)).contents.name = ":extra:$DATA"
        return int(fault == "ok" and calls == 1)

    def case(handle: int, kind: int, data: Any, size: int) -> int:
        assert kind == 23
        ctypes.cast(data, ctypes.POINTER(ctypes.c_uint32)).contents.value = 1
        return int(fault != "case")

    kernel = cast(
        Kernel,
        SimpleNamespace(
            FindFirstStreamW=first,
            FindNextStreamW=next_stream,
            GetLastError=lambda: 5 if fault in {"first", "next"} else 38,
            FindClose=lambda h: closed.append(h) or int(fault != "close"),
            CreateFileW=lambda *a: 42,
            GetFileInformationByHandleEx=case,
            CloseHandle=lambda h: closed.append(h) or 1,
        ),
    )
    if fault in {"first", "next", "close"}:
        with pytest.raises(OSError):
            list(stream_names(tmp_path, kernel=kernel))
    else:
        assert list(stream_names(tmp_path, kernel=kernel)) == (
            ["::$DATA", ":extra:$DATA"] if fault == "ok" else ["::$DATA"]
        )
    assert closed == ([] if fault == "first" else [42])
    if fault == "case":
        with pytest.raises(OSError):
            case_sensitive_directory(tmp_path, kernel=kernel)
        assert closed == [42, 42]
    elif fault == "ok":
        assert case_sensitive_directory(tmp_path, kernel=kernel)
        iterator = stream_names(tmp_path, kernel=kernel)
        next(iterator)
        iterator.close()
        assert closed == [42, 42, 42]


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows proof")
def test_native_file_information(tmp_path: Path) -> None:
    target = tmp_path / "plain"
    target.write_bytes(b"binary\r\n")
    assert list(stream_names(target)) == ["::$DATA"]
    assert not case_sensitive_directory(tmp_path)
