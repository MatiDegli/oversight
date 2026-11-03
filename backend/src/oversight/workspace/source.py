"""Workspace source selection. Profiles and prepared own-copy startup never fetch a source."""

import hashlib
import hmac
import ntpath
import os
import stat
import sys
import time
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from oversight.workspace._platform import executable, preflight_placement
from oversight.workspace.integrity import IntegrityError
from oversight.workspace.locks import offload
from oversight.workspace.metadata_io import Budget, Entry, read_entry, replace_file
from oversight.workspace.preview_launch_spec import FileIdentity, plain_path
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

OVERSIGHT_SELF = SourceProfile(
    name="oversight-self",
    upstream="reviewed local checkout; no startup fetch",
    commit="c08eca088bfa790c8273fe6e41c4e0f2e5f94902",
    checkout_dir="sources/oversight-self",
    provenance="oversight-self@c08eca08 (filtered disposable copy)",
    pytest_targets=("demo-repo/tests",),
    pytest_options=("-c", "demo-repo/pyproject.toml", "--rootdir=.", "--confcutdir=demo-repo"),
)
SELF_PIN_REFUSAL = "oversight-self: reviewed clean pinned checkout required"
SELF_INVENTORY_REFUSAL = "oversight-self: unsafe or incomplete source inventory"
_INVENTORY_KEY = os.urandom(32)


def _source_token(path: Path) -> tuple[int, ...]:
    plain_path(path)
    info = path.lstat()
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError(SELF_INVENTORY_REFUSAL)
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError(SELF_INVENTORY_REFUSAL)
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _self_git_files(root: Path, commit: str) -> tuple[tuple[FileIdentity, ...], tuple[Path, ...]]:
    head = root / ".git/HEAD"
    identities = [FileIdentity.read(head), FileIdentity.read(root / ".git/index")]
    text = head.read_text(encoding="ascii").strip()
    missing: tuple[Path, ...] = ()
    if text.startswith("ref: refs/heads/"):
        relative = text.removeprefix("ref: ")
        if any(p in {"", ".", ".."} for p in relative.split("/")) or "\\" in relative:
            raise ValueError(SELF_INVENTORY_REFUSAL)
        reference = root / ".git" / relative
        if reference.exists():
            identities.append(FileIdentity.read(reference))
        else:
            missing = (reference,)
            identities.append(FileIdentity.read(root / ".git/packed-refs"))
    elif text != commit:
        raise ValueError(SELF_INVENTORY_REFUSAL)
    return tuple(identities), missing


@dataclass(frozen=True)
class SourceInventory:
    root: Path
    commit: str
    entries: tuple[FileIdentity, ...]
    digest: str
    observed: tuple[tuple[Path, tuple[int, ...]], ...] = ()
    git_files: tuple[FileIdentity, ...] = ()
    missing_refs: tuple[Path, ...] = ()
    proof: bytes = field(default=b"", repr=False)

    def _signature(self) -> bytes:
        return hmac.digest(_INVENTORY_KEY, repr(replace(self, proof=b"")).encode(), "sha256")

    def verify(self) -> None:
        try:
            if not hmac.compare_digest(self.proof, self._signature()):
                raise ValueError(SELF_INVENTORY_REFUSAL)
            plain_path(self.root)
            for path, token in self.observed:
                if _source_token(path) != token:
                    raise ValueError(SELF_INVENTORY_REFUSAL)
            if any(os.path.lexists(path) for path in self.missing_refs):
                raise ValueError(SELF_INVENTORY_REFUSAL)
            for entry in (*self.entries, *self.git_files):
                entry.verify()
        except (OSError, ValueError, RuntimeError):
            raise ValueError(SELF_INVENTORY_REFUSAL) from None


def self_input_allowed(name: str) -> bool:
    from oversight.workspace.secret_paths import is_secret_path

    excluded = {
        ".git",
        "node_modules",
        ".runtime",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        ".shots",
        "htmlcov",
        ".tox",
        ".npm",
        ".pnpm-store",
        ".vite",
        "__pypackages__",
    }
    parts = name.split("/")
    return (
        not any(part.casefold() in excluded for part in parts)
        and name.casefold() != "frontend/dist"
        and not name.casefold().startswith("frontend/dist/")
        and not name.endswith((".pyc", ".pyo", ".coverage"))
        and not is_secret_path(name, nt=True)
    )


async def self_inventory(
    profile: SourceProfile, runtime: Path, *, spawn: Spawn = run_process
) -> SourceInventory:
    """Only this reviewed profile may provide the copier's tracked-file allowlist."""
    if profile != OVERSIGHT_SELF:
        raise ValueError(SELF_PIN_REFUSAL)
    try:
        root = await validate_source(profile, runtime, spawn=spawn)
    except ValueError:
        raise ValueError(SELF_PIN_REFUSAL) from None
    try:
        return await _self_inventory(profile, root, runtime, spawn)
    except (OSError, ValueError, RuntimeError):
        raise ValueError(SELF_INVENTORY_REFUSAL) from None


async def _self_inventory(
    profile: SourceProfile, root: Path, runtime: Path, spawn: Spawn
) -> SourceInventory:
    result = await spawn(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "ls-files",
            "--stage",
            "-z",
        ],
        cwd=root,
        timeout_s=30,
        env=SOURCE_GIT_ENV,
        output_cap=2 * 1024 * 1024,
    )
    try:
        if (
            result.returncode != 0
            or result.timed_out
            or result.truncated
            or not result.stdout
            or not result.stdout.endswith("\0")
        ):
            raise ValueError(SELF_INVENTORY_REFUSAL)
        entries: list[FileIdentity] = []
        observed: dict[Path, tuple[int, ...]] = {root: _source_token(root)}
        names: set[str] = set()
        total = 0
        for record in result.stdout[:-1].split("\0"):
            metadata, name = record.split("\t", 1)
            mode, blob, stage = metadata.split(" ")
            if (
                mode not in {"100644", "100755"}
                or stage != "0"
                or len(blob) != 40
                or any(c not in "0123456789abcdef" for c in blob)
                or name in names
                or len(names) >= 10000
                or "\ufffd" in name
                or not name
                or any(p in {"", ".", ".."} for p in name.split("/"))
                or "\\" in name
                or ":" in name
                or name.startswith("/")
                or any(ord(c) < 32 for c in name)
            ):
                raise ValueError(SELF_INVENTORY_REFUSAL)
            names.add(name)
            # Validate type even for excluded inputs, without opening their contents.
            path = root / name
            plain_path(path)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError(SELF_INVENTORY_REFUSAL)
            for observed_path in (path, *path.parents):
                if not observed_path.is_relative_to(root):
                    break
                token = _source_token(observed_path)
                if observed.setdefault(observed_path, token) != token:
                    raise ValueError(SELF_INVENTORY_REFUSAL)
            if self_input_allowed(name):
                entry = await offload(partial(FileIdentity.read, path))
                total += entry.token[2]
                if total > 128 * 1024 * 1024:
                    raise ValueError(SELF_INVENTORY_REFUSAL)
                entries.append(entry)
        git_files, missing_refs = await offload(partial(_self_git_files, root, profile.commit))
        inventory = SourceInventory(
            root,
            profile.commit,
            tuple(entries),
            hashlib.sha256(
                "".join(
                    f"{e.path.relative_to(root).as_posix()}\0{e.sha256}\0" for e in entries
                ).encode()
            ).hexdigest(),
            tuple(observed.items()),
            git_files,
            missing_refs,
        )
        inventory = replace(inventory, proof=inventory._signature())  # pyright: ignore[reportPrivateUsage]
        await offload(inventory.verify)
        await validate_source(profile, runtime, spawn=spawn)
        return inventory
    except (OSError, ValueError, RuntimeError):
        raise ValueError(SELF_INVENTORY_REFUSAL) from None


def select_source(name: str) -> SourceProfile | OwnSource | None:
    """Select demo/profile first; native Windows paths require prepared COPY startup.

    OwnSource is a raw selection, never permission to bypass preparation, validated staging
    or guarded suites. Arbitrary names and repository paths on POSIX remain refused.
    """
    if not name:
        return None
    profiles = {p.name: p for p in (BOLTONS, OVERSIGHT_SELF)}
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
    """Keep the self refusal fixed without changing other profiles' diagnostics."""
    try:
        return await _validate_pinned_source(profile, runtime_root, spawn=spawn)
    except (OSError, ValueError, RuntimeError):
        if profile.name == OVERSIGHT_SELF.name:
            raise ValueError(SELF_PIN_REFUSAL) from None
        raise


async def _validate_pinned_source(
    profile: SourceProfile, runtime_root: Path, *, spawn: Spawn
) -> Path:
    """Verify a real, clean pinned checkout before any runtime reset or graph export."""
    root = profile.checkout(runtime_root)
    if sys.platform == "win32":
        await offload(lambda: preflight_placement(root))
        await offload(lambda: preflight_placement(root / ".git"))
    if (
        root.resolve() != root
        or not (root / ".git").is_dir()
        or (root / ".git").is_symlink()
        or await offload(lambda: os.path.lexists(root / ".git/commondir"))
    ):
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
        if (
            head.returncode != 0
            or head.timed_out
            or head.truncated
            or head.stdout.strip() != profile.commit
        ):
            raise ValueError(f"{profile.name}: checkout must be at {profile.commit}. {FETCH_HINT}")
        status = await spawn(
            [binary, *flags, "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            timeout_s=30,
            env=SOURCE_GIT_ENV,
        )
        if status.returncode != 0 or status.timed_out or status.truncated or status.stdout.strip():
            raise ValueError(f"{profile.name}: dirty or unreadable checkout. {FETCH_HINT}")
    except OSError as error:
        raise ValueError(f"{profile.name}: cannot verify checkout. {FETCH_HINT}") from error
    return root
