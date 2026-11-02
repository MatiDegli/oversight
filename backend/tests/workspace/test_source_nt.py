"""Internal forced-NT inventory: Git may describe source bytes, never mutate them."""

import hashlib
import os
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from itertools import chain
from pathlib import Path

import pytest

from oversight.workspace import run_process
from oversight.workspace._platform import acquire_lock, release_lock
from oversight.workspace.errors import InvalidPath
from oversight.workspace.runner import ProcessResult
from oversight.workspace.source_nt import (
    Inventory,
    NtSource,
    inventory,
    selected,
    validate_selection,
)

HEAD = "a" * 40


@contextmanager
def source_case(tmp_path: Path) -> Iterator[tuple[NtSource, int]]:
    root, runtime = tmp_path / "source", tmp_path / "runtime"
    (root / ".git").mkdir(parents=True)
    runtime.mkdir()
    fd = acquire_lock(runtime / "server.lock")
    try:
        yield NtSource(root, runtime, r"C:\private\source", r"C:\private\runtime"), fd
    finally:
        release_lock(fd)


class Git:
    def __init__(self, paths: str = "a.py\0", stages: str | None = None) -> None:
        self.answers = [
            HEAD + "\n",
            stages if stages is not None else f"100644 {HEAD} 0\ta.py\0",
            paths,
            "cache/\0",
        ]
        self.calls: list[tuple[str, ...]] = []
        self.result: Callable[[ProcessResult], ProcessResult] = lambda r: r

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = 262144,
    ) -> ProcessResult:
        self.calls.append(tuple(argv))
        assert (
            argv[0] == r"C:\Git\git.exe" and 0 < timeout_s <= 10 and output_cap == 8 * 1024 * 1024
        )
        assert env is not None and env["GIT_OPTIONAL_LOCKS"] == "0"
        assert env["GIT_DIR"] == str(cwd / ".git") and env["GIT_WORK_TREE"] == str(cwd)
        assert env["GIT_CONFIG_GLOBAL"] == "nul" and env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert f"core.hooksPath={cwd.parent / 'runtime/server.lock/hooks'}" in argv
        assert {
            "core.fsmonitor=false",
            "core.protectNTFS=true",
            "core.autocrlf=false",
            "core.longpaths=true",
            "core.eol=lf",
        } <= set(argv)
        return self.result(ProcessResult(0, self.answers.pop(0), "", False, False, 0))


async def collect(source: NtSource, fd: int, git: Git) -> Inventory:
    return await inventory(
        source, git=r"C:\Git\git.exe", lock_fd=fd, spawn=git, ancestors=(source.root.parent,)
    )


@pytest.mark.anyio
async def test_inventory_allowlist_dedup_head_and_exclusions(tmp_path: Path) -> None:
    with source_case(tmp_path) as (source, fd):
        git = Git(
            "a.py\0a.py\0new.py\0.ENV/private.py\0.venv/x\0node_modules/x\0x.pyc\0",
            f"100644 {HEAD} 0\ta.py\0"
            + f"100755 {HEAD} 0\tdeleted.py\0"
            + f"100644 {HEAD} 0\tignored.py\0",
        )
        result = await collect(source, fd, git)
        assert result.paths == ("a.py", "deleted.py", "ignored.py", "new.py")
        assert result.head == HEAD and result.skipped_roots == 1
        commands = [call[19:] for call in git.calls]
        assert commands == [
            ("rev-parse", "--verify", "HEAD"),
            ("ls-files", "-s", "-z"),
            ("ls-files", "-z", "--cached", "--others", "--exclude-standard"),
            ("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"),
        ]


@pytest.mark.anyio
@pytest.mark.parametrize("stage,number", [("160000", 0), ("120000", 0), ("100644", 1)])
async def test_gitlinks_symlinks_conflicts(tmp_path: Path, stage: str, number: int) -> None:
    with source_case(tmp_path) as (source, fd), pytest.raises(OSError, match="index, conflict"):
        await collect(source, fd, Git(stages=f"{stage} {HEAD} {number}\tx\0"))


@pytest.mark.anyio
@pytest.mark.parametrize("marker", ["gitdir", "commondir", "nested", "ancestor"])
async def test_plain_git_and_nested_markers(tmp_path: Path, marker: str) -> None:
    with source_case(tmp_path) as (source, fd):
        if marker == "gitdir":
            (source.root / ".git").rmdir()
            (source.root / ".git").write_text("gitdir: elsewhere\n")
        elif marker == "commondir":
            (source.root / ".git/commondir").write_text("elsewhere\n")
        else:
            parent = source.root / "sub" if marker == "nested" else source.root.parent
            parent.mkdir(exist_ok=True)
            (parent / ".git").mkdir()
        with pytest.raises((OSError, InvalidPath)):
            await collect(source, fd, Git("sub/x.py\0"))


@pytest.mark.anyio
async def test_changed_sentinel_prevents_source_spawn(tmp_path: Path) -> None:
    with source_case(tmp_path) as (source, fd):
        sentinel = source.runtime / "server.lock"
        sentinel.rename(source.runtime / "old")
        sentinel.write_bytes(b"replacement")
        git = Git()
        with pytest.raises(Exception, match="sentinel changed"):
            await collect(source, fd, git)
        assert not git.calls


@pytest.mark.anyio
async def test_real_readonly_git_dirty_untracked_deleted_and_tracked_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with source_case(tmp_path) as (source, fd):
        root = source.root

        async def setup(*args: str) -> str:
            result = await run_process(["git", *args], cwd=root, timeout_s=10)
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        await setup("init", "-q")
        for name in ("a.py", "deleted.py", "ignored.py"):
            (root / name).write_bytes(b"original\r\n")
        await setup("add", ".")
        await setup(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Fixture",
        )
        (root / ".gitignore").write_text("ignored.py\ncache/\n")
        (root / "a.py").write_bytes(b"dirty\r\n")
        (root / "deleted.py").unlink()
        (root / "new.py").write_bytes(b"new")
        (root / ".ENV").mkdir()
        (root / ".ENV/private.py").write_bytes(b"secret")
        for cache in (".venv", "cache", "node_modules"):
            (root / cache).mkdir()
            os.link(root / ".ENV/private.py", root / cache / "hardlink")
        from oversight.workspace.files import open_nt

        def observed(path: Path):
            assert path.parent == root and not path.name.startswith(".ENV")
            return open_nt(path)

        monkeypatch.setattr("oversight.workspace.source_nt.open_nt", observed)
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

        async def native(
            argv: Sequence[str],
            *,
            cwd: Path,
            timeout_s: float,
            env: Mapping[str, str] | None = None,
            output_cap: int = 262144,
        ) -> ProcessResult:
            return await run_process(
                ["git", *argv[1:]],
                cwd=cwd,
                timeout_s=timeout_s,
                env=dict(env or {}) | {"GIT_CONFIG_GLOBAL": os.devnull},
                output_cap=output_cap,
            )

        result = await inventory(
            source, git=r"C:\Git\git.exe", lock_fd=fd, spawn=native, ancestors=(root.parent,)
        )
        assert result.paths == (".gitignore", "a.py", "deleted.py", "ignored.py", "new.py")
        checked = validate_selection(result, sensitive=lambda _: False, streams=unnamed)
        by_path = {entry.path: entry for entry in checked.entries}
        assert by_path["a.py"].hash == hashlib.sha256(b"dirty\r\n").hexdigest()
        assert by_path["deleted.py"].identity is None
        assert result.head == await setup("rev-parse", "HEAD")
        assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def listing(source: NtSource, paths: tuple[str, ...]) -> Inventory:
    return Inventory(source, HEAD, paths, frozenset(paths), 0, 10)


def unnamed(_: Path) -> Generator[str]:
    yield "::$DATA"


@pytest.mark.parametrize(
    "name",
    tuple(
        chain(
            (".GIT", ".g\u0131t", ".git.", ".git ", "GIT~1"),
            ("a.py:s", "C:x", "a/../b", "CON/x.py", "NUL/a.py", "COM1/b/", "sub/CON"),
            ("CON", "nul.txt", "COM1", "CONIN$", "CONOUT$.txt", "LPT1.txt", "COM¹"),
            (".env::$DATA", ".env.", ".env /x"),
        )
    ),
)
def test_nt_refusal_table(name: str) -> None:
    with pytest.raises((OSError, InvalidPath)):
        selected(name)


def test_selection_path_count_includes_ancestors() -> None:
    from oversight.workspace.source_nt import path_set, short_path

    paths = tuple(f"a/{i}" for i in range(16383))
    assert len(path_set(paths)) == 16384
    with pytest.raises(OSError, match="16,384"):
        path_set((*paths, "a/extra"))
    short_path("C:/" + "x" * 236)
    with pytest.raises(OSError, match="240 UTF-16"):
        short_path("C:/" + "x" * 237)
