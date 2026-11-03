"""Workspace source selection. Profiles and prepared own-copy startup never fetch a source."""

import hashlib
import ntpath
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from oversight.workspace._platform import executable, preflight_placement
from oversight.workspace.integrity import IntegrityError
from oversight.workspace.locks import offload
from oversight.workspace.metadata_io import Budget, Entry, read_entry, replace_file
from oversight.workspace.runner import Spawn, run_process
from oversight.workspace.source_config import check_source_config

if TYPE_CHECKING:
    from oversight.workspace.source_nt import NtSource

TRUST_POLICY = "own-copy-v1"
TRUST_NOTICE = (
    "Approval changes only this disposable copy. Tests/plugins run your code as your user, "
    "with network and no filesystem sandbox; they may write outside runtime. Windows path "
    "checks have concurrent-swap races; copies/logs inherit folder ACLs, possibly readable by "
    "others. Child Python with -I/cleared environment can import original source undetected. "
    "Use trusted code/private folders."
)


@dataclass(frozen=True)
class OwnSource:
    """Native Windows selection; prepare, stage and guarded suites remain mandatory."""

    raw: str
    provenance: str = "own-repository (disposable copy)"
    pytest_targets: tuple[str, ...] = ()
    pytest_options: tuple[str, ...] = ()

    def prepare(self, runtime: Path, ide: Path) -> "NtSource":
        # Local import keeps source_nt's existing profile dependency acyclic.
        from oversight.workspace.source_nt import prepare_source

        return prepare_source(self.raw, str(runtime), str(ide), ntpath.join(self.raw, ".venv"))


def outside_profile(source: "NtSource", profile: str | None) -> bool:
    if not profile:
        return True
    try:
        root, home = map(ntpath.normcase, (source.source_path, ntpath.normpath(profile)))
        return ntpath.commonpath((root, home)) != home
    except ValueError:
        return True


def remember_trust(
    source: "NtSource", providers: tuple[str, ...], outside: bool, *, policy: str = TRUST_POLICY
) -> bool:
    """Informational only. Unsafe metadata is neither followed nor replaced; disclose again."""
    key = (
        hashlib.sha256(
            repr(
                (
                    ntpath.normcase(source.source_path),
                    policy,
                    tuple(sorted(set(providers))),
                    outside,
                )
            ).encode("utf-8")
        )
        .hexdigest()
        .encode("ascii")
    )
    path = source.runtime / "own-trust"
    deadline = time.monotonic() + 2
    try:
        before = read_entry(path, Budget(deadline))
        if before is not None and (before.data is None or len(before.data) != 64):
            return False
        if before is not None and before.data == key:
            return True
        replace_file(path, before, Entry(0o600, key), deadline, cas=True)
    except (OSError, IntegrityError):
        pass
    return False


FETCH_HINT = "Run make workspace-fetch-boltons to prepare the pinned source."
SOURCE_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}


@dataclass(frozen=True)
class SourceProfile:
    name: str
    upstream: str
    commit: str
    checkout_dir: str
    provenance: str
    pytest_targets: tuple[str, ...]
    extra_requirements: tuple[str, ...] = ()
    pytest_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Boltons has no runtime dependencies; its dev group's pytest>=8 is satisfied by
        # tools/demo-venv-requirements.txt. Never silently reuse that venv for extra dependencies.
        if self.extra_requirements:
            raise ValueError("source extra requirements need an environment builder")

    def checkout(self, runtime_root: Path) -> Path:
        return runtime_root / self.checkout_dir


BOLTONS = SourceProfile(
    name="boltons-d31d821",
    upstream="https://github.com/mahmoud/boltons",
    commit="d31d821d56c8f7756af9c30a864077f2e5855321",
    checkout_dir="sources/boltons-d31d821",
    provenance="boltons@d31d821",
    pytest_targets=("boltons", "tests"),
    pytest_options=("--doctest-modules",),
)


def select_source(name: str) -> SourceProfile | OwnSource | None:
    """Select demo/profile first; native Windows paths require prepared COPY startup.

    OwnSource is a raw selection, never permission to bypass preparation, validated staging
    or guarded suites. Arbitrary names and repository paths on POSIX remain refused.
    """
    if not name:
        return None
    profiles = {BOLTONS.name: BOLTONS}
    if name in profiles:
        return profiles[name]
    if sys.platform == "win32" and (ntpath.splitdrive(name)[0] or name.startswith(("/", "\\"))):
        return OwnSource(name)
    raise ValueError(
        "OVERSIGHT_WORKSPACE_SOURCE must be empty (demo-repo), one of "
        f"{', '.join(profiles)}, or an absolute drive path on native Windows; got {name!r}"
    )


async def validate_source(
    profile: SourceProfile, runtime_root: Path, *, spawn: Spawn = run_process
) -> Path:
    """Verify a real, clean pinned checkout before any runtime reset or graph export."""
    root = profile.checkout(runtime_root)
    if sys.platform == "win32":
        await offload(lambda: preflight_placement(root))
        await offload(lambda: preflight_placement(root / ".git"))
    if root.resolve() != root or not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise ValueError(f"{profile.name}: missing or unsafe checkout at {root}. {FETCH_HINT}")
    binary = "git"
    if sys.platform == "win32":
        binary = await offload(lambda: executable("git"))
    try:
        await check_source_config(
            root / ".git",
            git=binary,
            spawn=spawn,
            env=SOURCE_GIT_ENV,
            time_left=lambda: 30,
            refuse=lambda reason: ValueError(f"{profile.name}: {reason}. {FETCH_HINT}"),
        )
        flags = ["-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}"]
        head = await spawn(
            [binary, *flags, "rev-parse", "HEAD"],
            cwd=root,
            timeout_s=30,
            env=SOURCE_GIT_ENV,
        )
        if head.returncode != 0 or head.timed_out or head.stdout.strip() != profile.commit:
            raise ValueError(f"{profile.name}: checkout must be at {profile.commit}. {FETCH_HINT}")
        status = await spawn(
            [binary, *flags, "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            timeout_s=30,
            env=SOURCE_GIT_ENV,
        )
        if status.returncode != 0 or status.timed_out or status.stdout.strip():
            raise ValueError(f"{profile.name}: dirty or unreadable checkout. {FETCH_HINT}")
    except OSError as error:
        raise ValueError(f"{profile.name}: cannot verify checkout. {FETCH_HINT}") from error
    return root
