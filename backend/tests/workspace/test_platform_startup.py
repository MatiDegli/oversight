"""Placement, deletion and startup environments with injected NT evidence on Linux."""

import os
import shutil
import stat
import sys
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from oversight.workspace import UnsafeDeletion, Workspace, WorkspaceBusy, core, files, runner
from oversight.workspace import _platform as platform


def test_posix_environment_and_git_flags_are_exact(monkeypatch, tmp_path, template):
    monkeypatch.setenv("PATH", "/explicit/path")
    assert runner.child_env({"HOME": "/home/demo"}) == {
        "PATH": "/explicit/path",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "HOME": "/home/demo",
    }
    workspace = Workspace(tmp_path / ".runtime", template, pytest_pin="9.1.1")
    assert workspace.git_flags == (
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.symlinks=false",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "diff.ignoreSubmodules=all",
    )
    assert platform.executable("git") == "git" and platform.executable("uv") == "uv"
    assert workspace._git_env(workspace.main, None, None)["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_nt_reduced_environment_and_uv_allowlist(monkeypatch, tmp_path, template):
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, "system-" + name)
    monkeypatch.setenv("META_API_KEY", "private-meta")
    monkeypatch.setenv("BASETEN_API_KEY", "private-baseten")
    monkeypatch.setattr(sys, "platform", "win32")
    env = runner.child_env({"HOME": "private-home"})
    assert all(env[k] == "private-home" for k in ("HOME", "TEMP", "TMP", "USERPROFILE"))
    assert env["PYTHONUTF8"] == "1" and env["LANG"] == env["LC_ALL"] == "C.UTF-8"
    assert env["SYSTEMROOT"] == "system-SYSTEMROOT"
    workspace = Workspace(tmp_path / ".runtime", template, pytest_pin="9.1.1")
    assert "APPDATA" in workspace._uv_env() and "LOCALAPPDATA" in workspace._uv_env()
    assert workspace._demo_python.parts[-2:] == ("Scripts", "python.exe")
    assert workspace._git_env(workspace.main, None, None)["GIT_CONFIG_GLOBAL"] == "nul"
    assert "META_API_KEY" not in env and "BASETEN_API_KEY" not in workspace._uv_env()


def test_nt_executables_exclude_cwd_and_empty_relative_path(tmp_path, monkeypatch):
    binary = tmp_path / "bin"
    binary.mkdir()
    (tmp_path / "git.exe").touch()
    (binary / "git.exe").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    assert platform.executable("git", path=os.pathsep.join(["", ".", str(binary)])) == str(
        binary / "git.exe"
    )
    with pytest.raises(FileNotFoundError):
        platform.executable("git", path=os.pathsep.join(["", ".", "relative"]))


@pytest.mark.anyio
async def test_nt_flags_forward_to_git_suite_and_sentinel_lease(tmp_path, template, monkeypatch):
    root = tmp_path / ".runtime"
    root.mkdir()
    fd = platform.acquire_lock(root / "server.lock")
    monkeypatch.setattr(sys, "platform", "win32")
    workspace = Workspace(root, template, pytest_pin="9.1.1")
    workspace._lock_fd = fd
    git, suite = AsyncMock(), AsyncMock()
    monkeypatch.setattr(core, "guarded_git", git)
    monkeypatch.setattr(core, "guarded_suite", suite)
    try:
        async with workspace.locks(main=True):
            await workspace.git(workspace.main, "init")
        workspace.main.root.mkdir()
        await workspace.run_tests(workspace.main, timeout_s=1)
        for call in (git.call_args, suite.call_args):
            flags = call.kwargs["flags"]
            assert f"core.hooksPath={root / 'server.lock/hooks'}" in flags
            assert all(
                flag in flags
                for flag in (
                    "core.protectNTFS=true",
                    "core.eol=lf",
                    "core.longpaths=true",
                    "core.autocrlf=false",
                )
            )
        (root / "server.lock").rename(root / "old.lock")
        (root / "server.lock").touch()
        with pytest.raises(WorkspaceBusy, match="sentinel"):
            async with workspace.locks(main=True), workspace.git_lease():
                pytest.fail("replaced sentinel reached Git")
    finally:
        os.close(fd)
        workspace._lock_fd = None


@pytest.mark.parametrize(
    "raw",
    [
        r"\\host\share\x",
        r"\\?\C:\x",
        r"\\.\C:\x",
        r"C:relative",
        r"C:\NUL",
        r"C:\COM1.txt",
        r"relative",
        r"C:\x:stream",
        r"C:\SHORT~1\x",
        "C:\\x.\\y",
        "C:\\x \\y",
    ],
)
def test_placement_lexical_refusal_has_zero_io(raw):
    query, lstat = Mock(), Mock()
    with pytest.raises(OSError):
        platform.preflight_placement(raw, nt=True, query=query, lstat=lstat, synced=())
    query.assert_not_called()
    lstat.assert_not_called()


@pytest.mark.parametrize(
    "volume,attrs",
    [
        ((4, "NTFS", r"\Device\HarddiskVolume1"), 0),
        ((3, "FAT32", r"\Device\HarddiskVolume1"), 0),
        ((3, "NTFS", r"\??\C:\elsewhere"), 0),
        ((3, "NTFS", r"\Device\HarddiskVolume1"), 0x400),
        ((3, "NTFS", r"\Device\HarddiskVolume1"), 0x1000),
        ((3, "NTFS", r"\Device\HarddiskVolume1"), 0x40000),
        ((3, "NTFS", r"\Device\HarddiskVolume1"), 0x400000),
    ],
)
def test_placement_volume_and_ancestor_refusals(volume, attrs):
    with pytest.raises(OSError):
        platform.preflight_placement(
            r"C:\private\.runtime",
            nt=True,
            synced=(),
            query=lambda drive: volume,
            lstat=lambda path: cast(
                os.stat_result, SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=attrs)
            ),
        )


def test_plain_placement_warns_outside_profile_and_refuses_sync(caplog):
    def check(path, synced):
        platform.preflight_placement(
            path,
            nt=True,
            synced=synced,
            query=lambda d: (3, "NTFS", r"\Device\HarddiskVolume1"),
            lstat=lambda p: cast(
                os.stat_result, SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)
            ),
            userprofile=r"C:\Users\demo",
        )

    check(r"C:\private\.runtime", ())
    assert "outside USERPROFILE" in caplog.text
    with pytest.raises(OSError, match="nonsynced"):
        check(r"C:\private\.runtime", (r"C:\private",))


@pytest.mark.parametrize("tag", [0xA000000C, 0x9000001A])
def test_nt_deletion_handles_pytest_current_link_and_preserves_outside(tmp_path, monkeypatch, tag):
    root = tmp_path / ".runtime"
    target, outside = root / "tmp", tmp_path / "outside"
    target.mkdir(parents=True)
    outside.mkdir()
    (outside / "sentinel").write_text("keep")
    (target / "pytest-current").symlink_to(outside, target_is_directory=True)
    real_scandir = os.scandir

    # Linux symlink stats have no Windows tag; supply the documented symlink tag.
    class Entry:
        def __init__(self, child):
            self.path, self.child = child.path, child

        def stat(self, *, follow_symlinks):
            info = self.child.stat(follow_symlinks=follow_symlinks)
            return SimpleNamespace(st_mode=info.st_mode, st_reparse_tag=tag)

    class Scan:
        def __enter__(self):
            self.scan = real_scandir(target)
            return [Entry(child) for child in self.scan]

        def __exit__(self, *args):
            self.scan.close()

    monkeypatch.setattr(os, "scandir", lambda path: Scan())
    cleanup = Mock()
    monkeypatch.setattr(shutil, "rmtree", cleanup)
    monkeypatch.setattr(sys, "platform", "win32")
    if tag != 0xA000000C:
        with pytest.raises(UnsafeDeletion, match="unknown"):
            files.remove_tree(root, target)
        cleanup.assert_not_called()
        return
    files.remove_tree(root, target)
    assert not (target / "pytest-current").is_symlink()
    assert (outside / "sentinel").read_text() == "keep"
    cleanup.assert_called_once()
    (target / "readonly").write_text("destination")
    (target / "readonly").chmod(0o400)
    error = PermissionError("read-only")
    error.__dict__["winerror"] = 5
    cleanup.call_args.kwargs["onexc"](os.unlink, str(target / "readonly"), error)
    assert not (target / "readonly").exists()
    with pytest.raises(UnsafeDeletion):
        files.remove_tree(root, outside)


@pytest.mark.anyio
async def test_capture_forwards_nt_flags_and_absolute_git(tmp_path, template, monkeypatch):
    from oversight.workspace import test_tree
    from oversight.workspace.runner import ProcessResult

    root = tmp_path / "tree"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "win32")
    workspace = Workspace(tmp_path / ".runtime", template, pytest_pin="9.1.1")
    spawn = AsyncMock(return_value=ProcessResult(0, "", "", False, False, 1))
    monkeypatch.setattr(test_tree, "executable", lambda name: "C:/tools/git.exe")
    monkeypatch.setattr(test_tree, "run_process", spawn)
    await test_tree.capture_test_tree(
        root,
        tmp_path,
        env={"GIT_DIR": str(root / ".git"), "GIT_COMMON_DIR": str(root / ".git")},
        flags=workspace.git_flags,
        timeout_s=3,
    )
    assert len(spawn.call_args_list) == 6
    for call in spawn.call_args_list:
        assert call.args[0][: 1 + len(workspace.git_flags)] == [
            "C:/tools/git.exe",
            *workspace.git_flags,
        ]
