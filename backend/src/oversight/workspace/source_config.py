"""Inspect source configuration without loading it as repository configuration."""

import ntpath
import os
import stat
import tempfile
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from oversight.workspace.locks import offload
from oversight.workspace.runner import NtCleanupError, Spawn

MAX_CONFIG_OUTPUT = 262_144
# Pointing Git at the original would make its early config read follow the original's includes.
REPOSITORY_ENV = frozenset({"GIT_DIR", "GIT_WORK_TREE"})
# Git expands these into absolute paths (a network home share on Windows).
EXPANDED_PREFIXES = ("~", "%(prefix)")


def refused_key(key: str, value: str) -> str | None:
    """Name of the setting that makes Git read outside the repository, never its value."""
    if key == "include.path":
        return "include.path"
    if key.startswith("includeif.") and key.endswith(".path"):
        return "includeIf.*.path"  # The subsection is a repository-chosen path.
    if key == "core.excludesfile" and (
        os.path.isabs(value)
        or ntpath.isabs(value)
        or ntpath.splitdrive(value)[0]
        or value.startswith(EXPANDED_PREFIXES)
    ):
        return "core.excludesFile"
    return None


@asynccontextmanager
async def _listing_directory(source_root: Path) -> AsyncGenerator[Path]:
    directory: tempfile.TemporaryDirectory[str] | None = None

    def create() -> Path:
        nonlocal directory
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if temporary_root.is_relative_to(source_root.resolve()):
            raise OSError("temporary directory is inside the source checkout")
        directory = tempfile.TemporaryDirectory(prefix="oversight-config-", dir=temporary_root)
        return Path(directory.name).resolve()

    try:
        yield await offload(create)
    finally:
        # Retain ownership even when cancellation arrives during creation or resolution.
        if directory is not None:
            await offload(directory.cleanup)


async def check_source_config(
    git_dir: Path,
    *,
    git: str,
    spawn: Spawn,
    env: Mapping[str, str],
    time_left: Callable[[], float],
    refuse: Callable[[str], Exception],
) -> None:
    """Fail closed before source queries; the caller owns the deadline and refusal type."""
    listing_env = {name: value for name, value in env.items() if name.upper() not in REPOSITORY_ENV}
    for name in ("config", "config.worktree"):
        path = git_dir / name
        label = f".git/{name}"
        try:
            mode = (await offload(path.lstat)).st_mode
        except FileNotFoundError:
            continue
        except OSError:
            raise refuse(f"{label}: cannot inspect Git config") from None
        if not stat.S_ISREG(mode):
            raise refuse(f"{label}: unsafe Git config file")
        try:
            async with _listing_directory(git_dir.parent) as listing_cwd:
                # The ceiling is the empty cwd's parent, so discovery checks only the empty cwd.
                listing_env["GIT_CEILING_DIRECTORIES"] = str(listing_cwd.parent)
                config_path = await offload(path.absolute)
                result = await spawn(
                    [
                        git,
                        "--no-pager",
                        "config",
                        "--file",
                        str(config_path),
                        "--no-includes",
                        "--null",
                        "--list",
                    ],
                    cwd=listing_cwd,
                    env=listing_env,
                    timeout_s=time_left(),
                    output_cap=MAX_CONFIG_OUTPUT,
                )
        except NtCleanupError:
            raise  # Keep the runner's cleanup signal intact across the COPY refusal boundary.
        except OSError:
            raise refuse(f"{label}: cannot list Git config") from None
        time_left()  # Include process cleanup and filesystem inspection in the caller's deadline.
        if result.returncode != 0 or result.timed_out or result.truncated:
            raise refuse(f"{label}: failed, timed-out or truncated Git config listing")
        text = result.stdout
        if (text and not text.endswith("\0")) or "\ufffd" in text:
            raise refuse(f"{label}: malformed Git config listing")
        for record in text[:-1].split("\0") if text else ():
            key, _, value = record.partition("\n")
            key = key.casefold()
            if not key or "." not in key:
                raise refuse(f"{label}: malformed Git config key")
            if (refused := refused_key(key, value)) is not None:
                raise refuse(f"{label}: refused Git config key {refused}")
