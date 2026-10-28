"""Server-owned workspace profiles. Selection and startup validation never fetch a source."""

import os
from dataclasses import dataclass
from pathlib import Path

from oversight.workspace.runner import Spawn, run_process

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
    pytest_targets=("--doctest-modules", "boltons", "tests"),
)


def select_source(name: str) -> SourceProfile | None:
    """Empty means the existing demo; no paths or model-provided argv are accepted."""
    if not name:
        return None
    profiles = {BOLTONS.name: BOLTONS}
    if name not in profiles:
        raise ValueError(
            "OVERSIGHT_WORKSPACE_SOURCE must be empty (demo-repo) or one of "
            f"{', '.join(profiles)}; got {name!r}"
        )
    return profiles[name]


async def validate_source(
    profile: SourceProfile, runtime_root: Path, *, spawn: Spawn = run_process
) -> Path:
    """Verify a real, clean pinned checkout before any runtime reset or graph export."""
    root = profile.checkout(runtime_root)
    if root.resolve() != root or not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise ValueError(f"{profile.name}: missing or unsafe checkout at {root}. {FETCH_HINT}")
    try:
        head = await spawn(["git", "rev-parse", "HEAD"], cwd=root, timeout_s=30, env=SOURCE_GIT_ENV)
        if head.returncode != 0 or head.timed_out or head.stdout.strip() != profile.commit:
            raise ValueError(f"{profile.name}: checkout must be at {profile.commit}. {FETCH_HINT}")
        status = await spawn(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            timeout_s=30,
            env=SOURCE_GIT_ENV,
        )
        if status.returncode != 0 or status.timed_out or status.stdout.strip():
            raise ValueError(f"{profile.name}: dirty or unreadable checkout. {FETCH_HINT}")
    except OSError as error:
        raise ValueError(f"{profile.name}: cannot verify checkout. {FETCH_HINT}") from error
    return root
