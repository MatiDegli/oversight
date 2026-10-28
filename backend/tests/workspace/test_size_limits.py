"""File admission checks actual descriptor bytes, even after a successful stat."""

import os
from pathlib import Path

import pytest

from oversight.limits import MAX_FILE_BYTES
from oversight.workspace import FileTooLarge
from oversight.workspace import ranges as ranges_module
from oversight.workspace.errors import WorkspaceError
from oversight.workspace.files import content_hash, read_file, write_file
from oversight.workspace.ranges import read_range


@pytest.mark.parametrize("extra", [0, 1])
def test_file_and_replacement_byte_boundary(tmp_path, extra) -> None:
    target = tmp_path / "a.txt"
    text = "é" * (MAX_FILE_BYTES // 2) + "x" * extra
    if extra:
        with pytest.raises(FileTooLarge):
            write_file(tmp_path, tmp_path, "a.txt", text, None)
        assert not target.exists()
    else:
        digest = write_file(tmp_path, tmp_path, "a.txt", text, None)
        assert read_file(tmp_path, "a.txt").hash == digest
    target.write_text(text)
    if extra:
        with pytest.raises(FileTooLarge):
            read_file(tmp_path, "a.txt")
    else:
        assert read_file(tmp_path, "a.txt").content == text


def test_grows_after_descriptor_stat_is_still_refused(tmp_path, monkeypatch) -> None:
    target = tmp_path / "growing.txt"
    target.write_text("small")
    original = os.fstat

    def grow(fd):
        info = original(fd)
        target.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        return info

    monkeypatch.setattr(os, "fstat", grow)
    with pytest.raises(FileTooLarge):
        read_file(tmp_path, "growing.txt")


def test_grows_after_path_stat_refused_without_a_temp_file(tmp_path, monkeypatch) -> None:
    target = tmp_path / "growing.txt"
    target.write_text("small")
    original = os.lstat

    def grow(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if Path(path) == target:
            target.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        return info

    monkeypatch.setattr(os, "lstat", grow)
    with pytest.raises(FileTooLarge):
        write_file(tmp_path, tmp_path, target.name, "replacement", content_hash(b"small"))
    assert not list(tmp_path.glob(".write-*"))
    assert target.stat().st_size == MAX_FILE_BYTES + 1


def test_streamed_range_preserves_line_rules_and_hash_with_split_utf8(tmp_path) -> None:
    text = "a" * 8191 + "é\r\nb\rc\nd\vlast\u2028"
    data = text.encode()
    (tmp_path / "a.txt").write_bytes(data)
    result = read_range(tmp_path, "a.txt", 2, 5, 40_000)
    assert result.lines == text.splitlines()[1:5]
    assert result.lengths == [len(line) for line in result.lines]
    assert result.total == len(text.splitlines())
    assert result.hash == content_hash(data)


def test_range_boundary_and_growth_after_stat(tmp_path, monkeypatch) -> None:
    cap = 8192
    monkeypatch.setattr(ranges_module, "MAX_RANGE_FILE_BYTES", cap)
    target = tmp_path / "growing.txt"
    target.write_bytes(b"x" * cap)
    assert read_range(tmp_path, target.name, 1, 1, 4).lengths == [cap]
    original = os.fstat
    calls = 0

    def grow(fd):
        nonlocal calls
        info = original(fd)
        calls += 1
        # open_file checks regular-file status first; grow after the range's own size check.
        if calls == 2:
            with target.open("ab") as handle:
                handle.write(b"x")
        return info

    monkeypatch.setattr(os, "fstat", grow)
    with pytest.raises(WorkspaceError, match=r"growing.txt exceeds the 8192-byte size limit"):
        read_range(tmp_path, target.name, 1, 1, 4)
    assert calls == 2
