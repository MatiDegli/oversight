"""Source config is listed in isolation before any query can act on its settings."""

import asyncio
import os
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from oversight.workspace.locks import offload
from oversight.workspace.runner import NtCleanupError, ProcessResult, run_process
from oversight.workspace.source import BOLTONS, FETCH_HINT, SOURCE_GIT_ENV, validate_source
from oversight.workspace.source_config import MAX_CONFIG_OUTPUT, check_source_config
from oversight.workspace.source_nt import inventory

from .test_source_nt import source_case

pytestmark = pytest.mark.anyio


async def check(git_dir: Path, env: Mapping[str, str] = SOURCE_GIT_ENV) -> None:
    await check_source_config(
        git_dir,
        git="git",
        spawn=run_process,
        env=env,
        time_left=lambda: 10,
        refuse=ValueError,
    )


@pytest.mark.parametrize("filename", ["config", "config.worktree"])
@pytest.mark.parametrize(
    "section,key,value,listed_key",
    [
        ("include", "path", "private-include", "include.path"),
        ('includeIf "gitdir:private-section"', "path", "private-include", "includeIf.*.path"),
        ("InClUdE", "PaTh", "private-include", "include.path"),
        ('InClUdEiF "gitdir:private-section"', "PaTh", "private-include", "includeIf.*.path"),
        ("core", "excludesFile", r"\\private-server\share\ignore", "core.excludesFile"),
        ("CoRe", "ExClUdEsFiLe", "//private-server/share/ignore", "core.excludesFile"),
        ("core", "excludesFile", "/private/ignore", "core.excludesFile"),
        ("core", "excludesFile", "C:/private/ignore", "core.excludesFile"),
        ("core", "excludesFile", "C:private-ignore", "core.excludesFile"),
        ("core", "excludesFile", "~/private-ignore", "core.excludesFile"),
        ("core", "excludesFile", "~private-user/ignore", "core.excludesFile"),
        ("core", "excludesFile", "%(prefix)/private-ignore", "core.excludesFile"),
    ],
)
async def test_real_git_refuses_keys_without_disclosing_values(
    tmp_path: Path, filename: str, section: str, key: str, value: str, listed_key: str
) -> None:
    git_dir = tmp_path / "source/.git"
    git_dir.mkdir(parents=True)
    # Single quotes are literal in Git config; escape backslashes inside double quotes.
    escaped = value.replace("\\", "\\\\")
    (git_dir / filename).write_text(f'[{section}]\n{key} = "{escaped}"\n')
    with pytest.raises(ValueError) as error:
        await check(git_dir)
    assert f".git/{filename}" in str(error.value)
    assert listed_key in str(error.value)
    assert value not in str(error.value)
    assert "private-section" not in str(error.value)


@pytest.mark.parametrize("worktree", [False, True])
async def test_real_git_accepts_ordinary_config_and_relative_excludes(
    tmp_path: Path, worktree: bool
) -> None:
    git_dir = tmp_path / "source/.git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text(
        '[core]\nbare = false\nexcludesFile = local-ignore\n[remote "origin"]\n'
        'url = https://example.invalid/project\n[branch "main"]\nremote = origin\n'
    )
    if worktree:
        (git_dir / "config.worktree").write_text("[core]\nexcludesFile = ../local-ignore\n")
    await check(git_dir)


async def test_missing_configs_need_no_git(tmp_path: Path) -> None:
    async def forbidden(*args: object, **kwargs: object) -> ProcessResult:
        pytest.fail("Git queried for missing config")

    await check_source_config(
        tmp_path / "source/.git",
        git="git",
        spawn=forbidden,
        env=SOURCE_GIT_ENV,
        time_left=lambda: 10,
        refuse=ValueError,
    )


@pytest.mark.parametrize("kind", ["file", "directory", "dangling-symlink"])
@pytest.mark.parametrize("config_present", [False, True])
async def test_pinned_commondir_refuses_before_any_spawn(
    tmp_path: Path, kind: str, config_present: bool
) -> None:
    git_dir = BOLTONS.checkout(tmp_path) / ".git"
    git_dir.mkdir(parents=True)
    if config_present:
        (git_dir / "config").touch()
    commondir = git_dir / "commondir"
    if kind == "file":
        commondir.write_text("private-common-directory\n")
    elif kind == "directory":
        commondir.mkdir()
    else:
        try:
            commondir.symlink_to(tmp_path / "private-missing-common-directory")
        except OSError:
            pytest.skip("symlinks unavailable")

    async def forbidden(*args: object, **kwargs: object) -> ProcessResult:
        pytest.fail("Git spawned before refusing commondir")

    with pytest.raises(ValueError, match="unsafe checkout") as error:
        await validate_source(BOLTONS, tmp_path, spawn=forbidden)
    assert FETCH_HINT in str(error.value)
    assert BOLTONS.name in str(error.value)
    assert "private-" not in str(error.value)


@pytest.mark.parametrize("relative", [False, True])
async def test_both_listings_use_empty_cwd_and_parent_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    git_dir = tmp_path / "source/.git"
    git_dir.mkdir(parents=True)
    for filename in ("config", "config.worktree"):
        (git_dir / filename).touch()
    supplied_env = {
        **SOURCE_GIT_ENV,
        "GIT_DIR": str(git_dir),
        "GIT_WORK_TREE": str(git_dir.parent),
        "GIT_CEILING_DIRECTORIES": str(tmp_path.parent),
        "CALLER_SETTING": "retained",
    }
    before = supplied_env.copy()
    calls: list[str] = []
    directories: list[Path] = []
    event_loop_thread = threading.get_ident()
    original_temporary_directory = tempfile.TemporaryDirectory

    def temporary_directory(*args, **kwargs):
        assert threading.get_ident() != event_loop_thread
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", temporary_directory)

    async def recording(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = 262144,
    ) -> ProcessResult:
        assert argv[0] == "caller-git"
        path = Path(argv[argv.index("--file") + 1])
        assert path.is_absolute() and path.parent == git_dir
        calls.append(path.name)
        assert not any(arg.startswith("--git-dir") for arg in argv)
        assert cwd == await offload(cwd.resolve)
        assert await offload(lambda: list(cwd.iterdir())) == []
        assert not cwd.is_relative_to(git_dir.parent)
        assert cwd not in directories and all(not previous.exists() for previous in directories)
        directories.append(cwd)
        assert env == {
            **SOURCE_GIT_ENV,
            "GIT_CEILING_DIRECTORIES": str(cwd.parent),
            "CALLER_SETTING": "retained",
        }
        assert timeout_s == 7.5
        assert output_cap == MAX_CONFIG_OUTPUT
        return ProcessResult(0, "", "", False, False, 0)

    monkeypatch.chdir(tmp_path)
    await check_source_config(
        Path("source/.git") if relative else git_dir,
        git="caller-git",
        spawn=recording,
        env=supplied_env,
        time_left=lambda: 7.5,
        refuse=ValueError,
    )
    assert calls == ["config", "config.worktree"]
    assert supplied_env == before
    assert all(not directory.exists() for directory in directories)


@pytest.mark.parametrize("failure", [asyncio.CancelledError, NtCleanupError])
async def test_listing_directory_removed_when_spawn_raises(tmp_path: Path, failure) -> None:
    git_dir = tmp_path / "source/.git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").touch()
    directories: list[Path] = []

    async def interrupted(argv, *, cwd, **kwargs) -> ProcessResult:
        directories.append(cwd)
        raise failure("interrupted")

    with pytest.raises(failure):
        await check_source_config(
            git_dir,
            git="git",
            spawn=interrupted,
            env=SOURCE_GIT_ENV,
            time_left=lambda: 10,
            refuse=ValueError,
        )
    assert len(directories) == 1
    assert all(not directory.exists() for directory in directories)


async def test_temporary_root_inside_source_is_refused_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_dir = tmp_path / "source/.git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").touch()
    monkeypatch.setattr(tempfile, "tempdir", str(git_dir.parent))

    async def forbidden(*args, **kwargs) -> ProcessResult:
        pytest.fail("Git spawned with a temporary directory inside the source")

    with pytest.raises(ValueError, match="cannot list Git config"):
        await check_source_config(
            git_dir,
            git="git",
            spawn=forbidden,
            env=SOURCE_GIT_ENV,
            time_left=lambda: 10,
            refuse=ValueError,
        )
    assert list(git_dir.parent.iterdir()) == [git_dir]


async def test_real_git_enclosing_repository_cannot_inject_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enclosing = tmp_path / "enclosing"
    enclosing.mkdir()
    result = await run_process(
        ["git", "init", "-q"], cwd=enclosing, timeout_s=10, env=SOURCE_GIT_ENV
    )
    assert result.returncode == 0, result.stderr
    (enclosing / ".git/config").write_text("[private-invalid-section\n")
    temporary_root = enclosing / "listing"
    temporary_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_root))
    git_dir = tmp_path / "source/.git"
    git_dir.mkdir(parents=True)
    for filename in ("config", "config.worktree"):
        (git_dir / filename).write_text("[core]\nexcludesFile = local-ignore\n")
    directories: list[Path] = []

    async def guarded(argv, *, cwd, env=None, **kwargs) -> ProcessResult:
        assert env is not None
        assert cwd.parent == temporary_root.resolve()
        directories.append(cwd)
        # With no ceiling, discovery reaches the enclosing repository's invalid config.
        control = await run_process(
            argv,
            cwd=cwd,
            env={k: v for k, v in env.items() if k != "GIT_CEILING_DIRECTORIES"},
            **kwargs,
        )
        assert control.returncode != 0
        assert "bad config" in control.stderr and ".git/config" in control.stderr
        return await run_process(argv, cwd=cwd, env=env, **kwargs)

    await check_source_config(
        git_dir,
        git="git",
        spawn=guarded,
        env={**SOURCE_GIT_ENV, "GIT_CEILING_DIRECTORIES": str(tmp_path)},
        time_left=lambda: 10,
        refuse=ValueError,
    )
    assert len(directories) == 2 and all(not directory.exists() for directory in directories)


@pytest.mark.parametrize("caller", ["inventory", "validation"])
@pytest.mark.parametrize("filename", ["config", "config.worktree"])
@pytest.mark.parametrize(
    "fault", ["include", "failed", "timeout", "truncated", "malformed", "oserror", "raised-timeout"]
)
async def test_refusal_precedes_all_source_queries(
    tmp_path: Path, caller: str, filename: str, fault: str
) -> None:
    calls: list[tuple[str, ...]] = []
    directories: list[Path] = []
    private = "private-value-must-not-appear"
    result = ProcessResult(0, f"include.path\n{private}\0", "", False, False, 0)
    if fault != "include":
        result = replace(
            result,
            stdout="core.bare\nfalse\0" if fault != "malformed" else "incomplete",
            returncode=1 if fault == "failed" else 0,
            timed_out=fault == "timeout",
            truncated=fault == "truncated",
            stderr=private,
        )

    async def recording(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = 262144,
    ) -> ProcessResult:
        calls.append(tuple(argv))
        assert tuple(argv[1:]) == (
            "--no-pager",
            "config",
            "--file",
            str(config),
            "--no-includes",
            "--null",
            "--list",
        )
        assert await offload(lambda: list(cwd.iterdir())) == []
        assert not cwd.is_relative_to(config.parent.parent)
        directories.append(cwd)
        assert 0 < timeout_s <= (10 if caller == "inventory" else 30)
        assert output_cap == MAX_CONFIG_OUTPUT
        assert env is not None
        assert env["GIT_CEILING_DIRECTORIES"] == str(cwd.parent)
        assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_OPTIONAL_LOCKS"] == "0"
        # Git must not be pointed at the original while it lists the original's config.
        assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
        if caller == "inventory":
            assert env["GIT_CONFIG_GLOBAL"] == "nul"
        else:
            assert env == {**SOURCE_GIT_ENV, "GIT_CEILING_DIRECTORIES": str(cwd.parent)}
        if fault in {"oserror", "raised-timeout"}:
            raise (OSError if fault == "oserror" else TimeoutError)(private)
        return result

    if caller == "inventory":
        with source_case(tmp_path) as (source, fd):
            config = source.root / ".git" / filename
            config.touch()
            with pytest.raises(OSError) as error:
                await inventory(
                    source, git=r"C:\Git\git.exe", lock_fd=fd, spawn=recording, ancestors=()
                )
    else:
        config = BOLTONS.checkout(tmp_path) / ".git" / filename
        config.parent.mkdir(parents=True)
        config.touch()
        with pytest.raises(ValueError) as error:
            await validate_source(BOLTONS, tmp_path, spawn=recording)
    assert len(calls) == 1
    assert all(not directory.exists() for directory in directories)
    assert private not in str(error.value)
    assert f".git/{filename}" in str(error.value)


async def test_real_git_parse_failure_refuses_without_stderr(tmp_path: Path) -> None:
    git_dir = tmp_path / "source/.git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text("[private-invalid-section\n")
    with pytest.raises(ValueError, match="failed") as error:
        await check(git_dir)
    assert "private-invalid-section" not in str(error.value)


@pytest.mark.parametrize(
    "section",
    ["include", 'includeIf "gitdir:**/source/.git"'],  # The condition matches the repo.
)
async def test_real_git_listing_never_reads_includes_of_the_original(
    tmp_path: Path, section: str
) -> None:
    """Pointed at the original (as inventory is), Git's early read would follow includes."""
    root = tmp_path / "source"
    root.mkdir()
    # Inventory's env, so only this check's own env handling can keep Git away from the include.
    env = {**SOURCE_GIT_ENV, "GIT_DIR": str(root / ".git"), "GIT_WORK_TREE": str(root)}
    init = await run_process(
        ["git", "init", "-q", str(root)], cwd=tmp_path, timeout_s=10, env=SOURCE_GIT_ENV
    )
    assert init.returncode == 0, init.stderr
    target = tmp_path / "private-invalid-include"
    target.write_text("[private-invalid-section\n")  # Git dies if it ever reads this file.
    escaped = str(target).replace("\\", "\\\\")
    with (root / ".git/config").open("a") as config:
        config.write(f'[{section}]\npath = "{escaped}"\n')
    with pytest.raises(ValueError, match=r"refused Git config key include") as error:
        await check(root / ".git", env)
    assert "failed" not in str(error.value)
    assert "private-" not in str(error.value) and "gitdir" not in str(error.value)


async def test_inventory_config_listing_shares_selection_deadline(tmp_path: Path) -> None:
    with source_case(tmp_path) as (source, fd):
        (source.root / ".git/config").touch()
        (source.root / ".git/config.worktree").touch()
        elapsed = 2.0
        timeouts: list[float] = []

        async def recording(
            argv: Sequence[str], *, timeout_s: float, **kwargs: object
        ) -> ProcessResult:
            nonlocal elapsed
            assert argv[1:3] == ["--no-pager", "config"]
            timeouts.append(timeout_s)
            elapsed += 5
            return ProcessResult(0, "core.bare\nfalse\0", "", False, False, 0)

        with pytest.raises(OSError, match="deadline"):
            await inventory(
                source,
                git=r"C:\Git\git.exe",
                lock_fd=fd,
                spawn=recording,
                ancestors=(),
                clock=lambda: elapsed,
                _deadline=10,
            )
        assert timeouts == [8, 3]


@pytest.mark.parametrize("kind", ["directory", "symlink"])
async def test_unsafe_config_file_refuses(tmp_path: Path, kind: str) -> None:
    git_dir = tmp_path / "source/.git"
    git_dir.mkdir(parents=True)
    config = git_dir / "config"
    if kind == "directory":
        config.mkdir()
    else:
        try:
            config.symlink_to(tmp_path / "missing-target")
        except OSError:
            pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match=r".git/config: unsafe"):
        await check(git_dir)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fsmonitor script")
async def test_pinned_fsmonitor_is_disabled_and_checkout_stays_clean(tmp_path: Path) -> None:
    root = BOLTONS.checkout(tmp_path)
    root.mkdir(parents=True)

    async def git(*args: str) -> str:
        result = await run_process(["git", *args], cwd=root, timeout_s=10, env=SOURCE_GIT_ENV)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    await git("init", "-q")
    (root / "value.txt").write_bytes(b"value\r\n")
    await git("config", "core.autocrlf", "true")
    await git("add", "value.txt")
    await git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "Fixture",
    )
    profile = replace(BOLTONS, commit=await git("rev-parse", "HEAD"))
    marker = root / ".git/fsmonitor-marker"
    script = root / ".git/fsmonitor.sh"
    script.write_text('#!/bin/sh\ntouch "$(dirname "$0")/fsmonitor-marker"\n')
    script.chmod(0o700)
    await git("config", "core.fsmonitor", str(script))
    calls: list[tuple[str, ...]] = []

    async def recording(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = 262144,
    ) -> ProcessResult:
        calls.append(tuple(argv))
        return await run_process(argv, cwd=cwd, timeout_s=timeout_s, env=env, output_cap=output_cap)

    assert await validate_source(profile, tmp_path, spawn=recording) == root
    assert not marker.exists()
    flags = ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}")
    assert calls[1:] == [
        ("git", *flags, "rev-parse", "HEAD"),
        ("git", *flags, "status", "--porcelain", "--untracked-files=all"),
    ]
