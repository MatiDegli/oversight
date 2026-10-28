import os
import stat
from pathlib import Path
from typing import Any

import pytest

from oversight.workspace.errors import InvalidPath, StaleFile, UnsafeDeletion, WorkspaceError
from oversight.workspace.files import (
    content_hash,
    copy_template,
    list_files,
    read_file,
    remove_tree,
    resolve_in,
    validate_runtime_root,
    write_file,
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    (tmp_path / "tmp").mkdir()
    return root


def test_content_hash_is_sha256_of_the_exact_bytes() -> None:
    assert content_hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert content_hash(b"a\r\n") != content_hash(b"a\n")


@pytest.mark.parametrize("path", ["a.py", "pkg/mod.py", "a b/c d.txt", "deep/er/new/file.txt"])
def test_plain_relative_paths_resolve_under_the_root(tree: Path, path: str) -> None:
    assert resolve_in(tree, path) == tree / path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../x",
        "a/../b",
        "./a",
        "a/./b",
        "a//b",
        "a/",
        "a\\b",
        "C:/x",
        "c:x",
        ".git/config",
        "sub/.git/config",
        "a\x00b",
        "a\nb",
        "x" * 1024,
    ],
)
def test_paths_that_break_a_rule_are_refused_without_naming_the_location(
    tree: Path, path: str
) -> None:
    with pytest.raises(InvalidPath) as refused:
        resolve_in(tree, path)
    assert str(tree) not in str(refused.value)
    assert "/etc/passwd" not in str(refused.value)


def test_symlinks_are_refused_wherever_they_are_and_whatever_they_point_to(
    tree: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (tree / "real").mkdir()
    (tree / "dirlink").symlink_to(outside, target_is_directory=True)
    (tree / "filelink").symlink_to(outside / "secret.txt")
    (tree / "inner").symlink_to(tree / "real", target_is_directory=True)
    for path in ["dirlink", "dirlink/secret.txt", "dirlink/new.txt", "filelink", "inner/x.txt"]:
        with pytest.raises(InvalidPath) as refused:
            resolve_in(tree, path)
        assert str(tmp_path) not in str(refused.value)


def test_write_text_hash_rules_leave_no_temp_file(tree: Path, tmp_path: Path) -> None:
    tmp_dir = tmp_path / "tmp"
    first = write_file(tree, tmp_dir, "pkg/a.py", "x = 1\n", None)
    assert first == content_hash(b"x = 1\n")
    assert read_file(tree, "pkg/a.py").content == "x = 1\n"
    with pytest.raises(StaleFile):
        write_file(tree, tmp_dir, "pkg/a.py", "x = 2\n", None)
    with pytest.raises(StaleFile):
        write_file(tree, tmp_dir, "pkg/a.py", "x = 2\n", content_hash(b"other"))
    with pytest.raises(StaleFile):
        write_file(tree, tmp_dir, "pkg/missing.py", "x = 2\n", content_hash(b"x = 1\n"))
    assert read_file(tree, "pkg/a.py").content == "x = 1\n"
    second = write_file(tree, tmp_dir, "pkg/a.py", "x = 2\n", first)
    assert read_file(tree, "pkg/a.py").hash == second
    assert list(tmp_dir.iterdir()) == []


@pytest.mark.parametrize("path", ["missing.py", "a/b/c/missing.py"])
def test_stale_missing_file_creates_no_directories(tree: Path, tmp_path: Path, path: str) -> None:
    with pytest.raises(StaleFile, match=f"{path} no longer exists"):
        write_file(tree, tmp_path / "tmp", path, "x = 2\n", content_hash(b"x = 1\n"))
    assert list(tree.iterdir()) == []
    assert list((tmp_path / "tmp").iterdir()) == []


def test_write_text_keeps_the_mode_of_an_existing_file_and_hashes_bytes_on_disk(
    tree: Path, tmp_path: Path
) -> None:
    script = tree / "run.sh"
    script.write_bytes(b"#!/bin/sh\r\n")
    script.chmod(0o755)
    write_file(tree, tmp_path / "tmp", "run.sh", "#!/bin/sh\n", content_hash(b"#!/bin/sh\r\n"))
    assert stat.S_IMODE(script.stat().st_mode) == 0o755
    write_file(tree, tmp_path / "tmp", "new.txt", "hello", None)
    assert stat.S_IMODE((tree / "new.txt").stat().st_mode) == 0o644


def test_read_refuses_a_file_that_is_not_utf8_text(tree: Path) -> None:
    (tree / "blob.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(WorkspaceError, match="UTF-8"):
        read_file(tree, "blob.bin")


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The descriptors `os.open` returns from now on, in the order they were opened."""
    fds: list[int] = []
    real = os.open

    def recording(*args: Any, **kwargs: Any) -> int:
        fd = real(*args, **kwargs)
        fds.append(fd)
        return fd

    monkeypatch.setattr(os, "open", recording)
    return fds


def is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_read_refuses_what_is_not_a_regular_file_and_closes_its_descriptor(
    tree: Path, opened: list[int], kind: str
) -> None:
    if kind == "directory":
        (tree / "special").mkdir()
    else:
        os.mkfifo(tree / "special")
    for _ in range(5):
        with pytest.raises(InvalidPath, match="not a regular file"):
            read_file(tree, "special")

    assert len(opened) == 5 and not any(is_open(fd) for fd in opened)


def test_read_closes_its_descriptor_after_a_file_it_can_and_cannot_decode(
    tree: Path, opened: list[int]
) -> None:
    (tree / "ok.txt").write_text("fine")
    (tree / "blob.bin").write_bytes(b"\xff\xfe\x00")
    assert read_file(tree, "ok.txt").content == "fine"
    with pytest.raises(WorkspaceError, match="UTF-8"):
        read_file(tree, "blob.bin")

    assert len(opened) == 2 and not any(is_open(fd) for fd in opened)


def test_write_never_follows_a_link_into_another_directory(tree: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tree / "dirlink").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidPath):
        write_file(tree, tmp_path / "tmp", "dirlink/pwn.txt", "x", None)
    with pytest.raises(InvalidPath):
        write_file(tree, tmp_path / "tmp", "dirlink/deeper/pwn.txt", "x", None)
    assert list(outside.iterdir()) == []
    assert list((tmp_path / "tmp").iterdir()) == []


def test_list_files_is_sorted_and_skips_git_caches_and_symlinks(tree: Path, tmp_path: Path) -> None:
    for name in ["b.py", "a.py", "pkg/z.py", "pkg/__pycache__/z.pyc", ".git/config", "c.pyc"]:
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    (tree / ".pytest_cache").mkdir()
    (tree / ".pytest_cache" / "v").write_text("x")
    (tree / "link.py").symlink_to(tree / "a.py")
    (tree / "dirlink").symlink_to(tmp_path, target_is_directory=True)
    assert list_files(tree) == ["a.py", "b.py", "pkg/z.py"]


def test_list_files_skips_the_git_file_of_a_worktree(tree: Path) -> None:
    (tree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/agent-1\n")
    (tree / "a.py").write_text("x")
    assert list_files(tree) == ["a.py"]


def test_copy_template_skips_caches_and_writes_plain_modes(tmp_path: Path) -> None:
    template = tmp_path / "template"
    for name in ["m.py", "pkg/n.py", "pkg/__pycache__/n.pyc", ".venv/bin/x", ".git/HEAD", "k.pyc"]:
        path = template / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    (template / "m.py").chmod(0o755)
    destination = tmp_path / "copy"
    copy_template(template, destination)
    assert list_files(destination) == ["m.py", "pkg/n.py"]
    assert stat.S_IMODE((destination / "m.py").stat().st_mode) == 0o644
    assert not (destination / ".git").exists()


def test_copy_template_refuses_a_symlink(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "real.py").write_text("x")
    (template / "link.py").symlink_to(template / "real.py")
    with pytest.raises(WorkspaceError, match="symbolic link"):
        copy_template(template, tmp_path / "copy")


# --- Deletion ---------------------------------------------------------------------------------


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / ".runtime"
    for name in ["workspace", "worktrees/agent-1", "tmp", "demo-venv", "runs"]:
        (root / name).mkdir(parents=True)
        (root / name / "keep.txt").write_text("x")
    (root / "ledger.sqlite").write_text("x")
    return root


def test_remove_tree_deletes_exactly_the_allowed_targets(runtime: Path) -> None:
    validate_runtime_root(runtime)
    for target in [
        runtime / "worktrees" / "agent-1",
        runtime / "worktrees",
        runtime / "workspace",
        runtime / "tmp",
        runtime / "demo-venv",
    ]:
        remove_tree(runtime, target)
        assert not target.exists()
    assert (runtime / "runs" / "keep.txt").exists()
    assert (runtime / "ledger.sqlite").exists()
    remove_tree(runtime, runtime / "workspace")


def test_remove_tree_refuses_outside_targets_and_deletes_nothing(
    runtime: Path, tmp_path: Path
) -> None:
    sibling = tmp_path / ".runtime-other"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("x")
    for target in [
        Path("/"),
        Path.home(),
        runtime,
        runtime.parent,
        sibling,
        runtime / "runs",
        runtime / "workspace" / "shop",
        runtime / "worktrees" / "agent-0",
        runtime / "worktrees" / "agent-1" / "shop",
        runtime / "worktrees" / "agent-1" / ".." / "agent-1",
        runtime / "worktrees" / "feature",
        Path("workspace"),
    ]:
        with pytest.raises(UnsafeDeletion):
            remove_tree(runtime, target)
    assert (sibling / "keep.txt").exists()
    for name in ["workspace", "worktrees/agent-1", "tmp", "demo-venv", "runs"]:
        assert (runtime / name / "keep.txt").exists()


def test_a_bad_runtime_root_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "real" / ".runtime"
    real.mkdir(parents=True)
    link_parent = tmp_path / "link"
    link_parent.symlink_to(tmp_path / "real", target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path / ".runtime"))
    bad = [
        Path(".runtime"),
        tmp_path / "not-runtime",
        link_parent / ".runtime",
        real / ".." / ".runtime",
        tmp_path / ".runtime",
        Path("/"),
    ]
    for root in bad:
        with pytest.raises(UnsafeDeletion):
            validate_runtime_root(root)
    monkeypatch.setenv("HOME", str(tmp_path / ".runtime" / "home"))
    with pytest.raises(UnsafeDeletion):
        validate_runtime_root(tmp_path / ".runtime")
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    validate_runtime_root(real)


def test_remove_tree_refuses_a_symlinked_target_or_ancestor_and_never_follows_inner_links(
    runtime: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    (outside / "agent-1").mkdir(parents=True)
    (outside / "agent-1" / "keep.txt").write_text("x")
    (outside / "keep.txt").write_text("x")
    # A symlinked target.
    (runtime / "tmp").rename(runtime / "tmp-real")
    (runtime / "tmp").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeDeletion):
        remove_tree(runtime, runtime / "tmp")
    # A symlinked ancestor.
    (runtime / "worktrees").rename(runtime / "worktrees-real")
    (runtime / "worktrees").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeDeletion):
        remove_tree(runtime, runtime / "worktrees" / "agent-1")
    assert (outside / "agent-1" / "keep.txt").exists()
    assert (outside / "keep.txt").exists()
    # Inner links are removed as links; what they point to survives.
    (runtime / "workspace" / "dirlink").symlink_to(outside, target_is_directory=True)
    (runtime / "workspace" / "filelink").symlink_to(outside / "keep.txt")
    remove_tree(runtime, runtime / "workspace")
    assert not (runtime / "workspace").exists()
    assert (outside / "keep.txt").exists()
    assert (outside / "agent-1" / "keep.txt").exists()
    assert os.path.islink(runtime / "tmp")


def test_only_uuid_probe_checkouts_are_deletable_under_tmp(runtime: Path) -> None:
    probe = runtime / "tmp" / ("base-probe-" + "a" * 32)
    probe.mkdir()
    (probe / "file").write_text("temporary checkout")
    remove_tree(runtime, probe)
    assert not probe.exists()
    for name in ("base-probe-bad", "other", "base-probe-" + "A" * 32):
        target = runtime / "tmp" / name
        target.mkdir()
        with pytest.raises(UnsafeDeletion):
            remove_tree(runtime, target)
        assert target.exists()
