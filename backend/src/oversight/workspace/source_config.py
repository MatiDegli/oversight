"""Inspect source configuration without loading it as repository configuration."""

import ntpath
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

from oversight.workspace.locks import offload
from oversight.workspace.runner import NtCleanupError, Spawn

MAX_CONFIG_OUTPUT = 262_144


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
            result = await spawn(
                [git, "config", "--file", str(path), "--no-includes", "--null", "--list"],
                cwd=git_dir.parent.parent,
                env=env,
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
            if (
                key == "include.path"
                or (key.startswith("includeif.") and key.endswith(".path"))
                or (
                    key == "core.excludesfile"
                    and (os.path.isabs(value) or ntpath.isabs(value) or ntpath.splitdrive(value)[0])
                )
            ):
                raise refuse(f"{label}: refused Git config key {key}")
