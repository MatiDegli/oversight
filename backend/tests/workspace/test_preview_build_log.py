"""Captured diagnostics stay bounded, inert and in supervisor-owned storage."""

import os
from pathlib import Path

import pytest

from oversight.workspace.preview_build_log import SanitizedBuildLog


def log_path(root: Path) -> Path:
    path = root / "runtime/previews/example/logs/build.log"
    path.parent.mkdir(parents=True)
    return path


def test_sanitized_build_log_bounds_and_paths(tmp_path):
    path = log_path(tmp_path)
    log = SanitizedBuildLog(path, ("sensitive-token", "secret界"))
    log.write(
        "start\x1b[31m sensitive-token\x1b[0m\x00\r\n\x1b]0;private title\x07safe\u202e\x9b31m界"
    )
    assert path.read_text() == "start \nsafe界"
    assert path.stat().st_mode & 0o777 == 0o600
    log.write("界" * 349500 + "secret界\x1b[31m" + "é" * 600000 + "\nend")
    data = path.read_bytes()
    text = data.decode("utf-8", errors="strict")
    assert len(data) <= 1024 * 1024
    assert "omitted" in text and text.endswith("\nend")
    assert "secret" not in text and "\x1b" not in text
    log.write("replacement")
    assert path.read_bytes() == b"replacement"


@pytest.mark.parametrize("attack", ["file-link", "hardlink", "directory-link", "replace-parent"])
def test_hostile_log_replacement(tmp_path, attack):
    path = log_path(tmp_path)
    log = SanitizedBuildLog(path)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim"
    victim.write_text("untouched")
    if attack == "file-link":
        path.symlink_to(victim)
    elif attack == "hardlink":
        os.link(victim, path)
    else:
        path.parent.rename(path.parent.with_name("old-logs"))
        if attack == "directory-link":
            path.parent.symlink_to(outside, target_is_directory=True)
        else:
            path.parent.mkdir()
    with pytest.raises((ValueError, OSError)):
        log.write("must not escape")
    assert victim.read_text() == "untouched"
    assert not (outside / "build.log").exists()


def test_invalid_log_paths_and_sanitization_order(tmp_path):
    with pytest.raises(ValueError):
        SanitizedBuildLog(tmp_path / "build.log")
    path = log_path(tmp_path)
    log = SanitizedBuildLog(path, ("abc", "x\x00y"))
    log.write("a\x00bc x\x00y xy\x1bPignored\x1b\\ok\ud800")
    assert path.read_bytes() == b"  ok"
    path.unlink()
    path.mkdir()
    with pytest.raises((ValueError, OSError)):
        log.write("refuse directory")


def test_redaction_cannot_recreate_secrets_or_leak_through_marker(tmp_path):
    path = log_path(tmp_path)
    log = SanitizedBuildLog(path, ("aba", "XX", "bytes"))
    log.write("abXXa " + "界" * 400000)
    text = path.read_text()
    assert all(secret not in text for secret in log.secrets)


def test_person_can_raise_diagnostic_budget(tmp_path):
    path = log_path(tmp_path)
    log = SanitizedBuildLog(path, max_bytes=2 * 1024**2)
    log.write("x" * (1024**2 + 10))
    assert path.stat().st_size == 1024**2 + 10
