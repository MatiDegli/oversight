"""Placement checks run on Linux without resolving a Windows input."""

import os
from functools import partial
from pathlib import Path

import pytest

from oversight.workspace._platform import preflight_placement
from oversight.workspace.errors import InvalidPath
from oversight.workspace.source import BOLTONS
from oversight.workspace.source_nt import overlaps, prepare_source


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
    assert len(paths) == 2 and result.source_path == r"C:\source"
    assert "outside USERPROFILE" in caplog.text
