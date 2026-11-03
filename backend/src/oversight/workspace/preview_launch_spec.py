"""Closed, person-selected version-1 reviewed Oversight launch data; never a launcher."""

import hashlib
import hmac
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import cast

OVERRIDE_REFUSAL = (
    "OVERSIGHT_PREVIEW_APP must be an absolute path to a UTF-8 version-1 JSON declaration"
)
INVALID = "preview declaration is invalid"
UNSUPPORTED = "preview application is not supported"
_SPEC_KEY = os.urandom(32)
BUILD = (
    (("{node}", "{tsc}", "--noEmit"), "frontend"),
    (("{node}", "{vite}", "build", "--outDir", "{state}/dist"), "frontend"),
)
START = (
    (
        "{python}",
        "-m",
        "uvicorn",
        "oversight.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "{public_port}",
    ),
    ".",
)
READINESS = {
    "api": "http://127.0.0.1:{api_port}/health",
    "public": "http://127.0.0.1:{public_port}/",
}


def plain_path(path: Path) -> None:
    """Reject links in any component, including cycles, before opening a declaration."""
    if not path.is_absolute() or path != Path(os.path.normpath(path)):
        raise ValueError(INVALID)
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(INVALID)


def _future_path(path: Path) -> None:
    """A frozen future disposable path must have plain existing ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(INVALID)
    str(path).encode("utf-8", errors="strict")
    for ancestor in (*reversed(path.parents), path):
        if os.path.lexists(ancestor):
            info = ancestor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError(INVALID)


@dataclass(frozen=True)
class FileIdentity:
    path: Path
    sha256: str
    token: tuple[int, ...]

    @classmethod
    def read(cls, path: Path, *, executable: bool = False) -> "FileIdentity":
        # Person-owned executables may be venv links; freeze both resolved identity and bytes.
        path = path.resolve(strict=True) if executable else path
        plain_path(path)
        before = path.stat()
        cap = 256 * 1024 * 1024
        if not stat.S_ISREG(before.st_mode) or before.st_size > cap:
            raise ValueError(INVALID)
        if executable and not os.access(path, os.X_OK):
            raise ValueError(INVALID)
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > cap:
                    raise ValueError(INVALID)
                digest.update(chunk)
        after = path.stat()

        def token(info: os.stat_result) -> tuple[int, ...]:
            return (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                info.st_mode,
                info.st_nlink,
            )

        if token(before) != token(after) or size != before.st_size:
            raise ValueError(INVALID)
        return cls(
            path,
            digest.hexdigest(),
            token(before),
        )

    def verify(self) -> None:
        if self != self.read(self.path):
            raise ValueError(INVALID)


@dataclass(frozen=True)
class PreviewCommand:
    argv: tuple[str, ...]
    cwd: Path
    timeout_s: int


@dataclass(frozen=True)
class PreviewLaunchSpec:
    mode: str
    build: tuple[PreviewCommand, ...]
    start: PreviewCommand
    api_port: int
    public_port: int
    provenance: str
    declaration: FileIdentity
    executables: tuple[FileIdentity, ...]
    dependencies: tuple[FileIdentity, ...]
    original: Path
    resources: tuple[Path, ...]
    readiness: tuple[str, str]
    state: Path
    proof: bytes = field(default=b"", repr=False)

    def _signature(self) -> bytes:
        return hmac.digest(_SPEC_KEY, repr(replace(self, proof=b"")).encode(), "sha256")

    def verify(self) -> None:
        if not hmac.compare_digest(self.proof, self._signature()):
            raise ValueError(INVALID)
        for command in (*self.build, self.start):
            _future_path(command.cwd)
        _future_path(self.state)
        if tuple(p.resolve(strict=True) for p in self.resources) != tuple(
            identity.path for identity in self.executables
        ):
            raise ValueError(INVALID)
        for identity in (self.declaration, *self.executables, *self.dependencies):
            identity.verify()


def _closed(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(INVALID)
        result[key] = value
    return result


def _command(
    raw: object, template: tuple[tuple[str, ...], str], substitutions: Mapping[str, str], tree: Path
) -> PreviewCommand:
    if not isinstance(raw, dict):
        raise ValueError(INVALID)
    raw = cast(dict[str, object], raw)
    if set(raw) != {"argv", "cwd", "timeout_s"}:
        raise ValueError(INVALID)
    argv, cwd = template
    timeout = raw["timeout_s"]
    if (
        raw["argv"] != list(argv)
        or raw["cwd"] != cwd
        or type(timeout) is not int
        or not 1 <= timeout <= 900
    ):
        raise ValueError(INVALID)
    if PurePosixPath(cwd).is_absolute() or ".." in PurePosixPath(cwd).parts:
        raise ValueError(INVALID)
    expanded = tuple(arg.format_map(substitutions) for arg in argv)
    return PreviewCommand(expanded, tree / cwd, timeout)


def load_preview_app(
    original: Path,
    values: Mapping[str, str],
    *,
    tree: Path | None = None,
    state: Path | None = None,
    python: Path | None = None,
    node: Path | None = None,
    tsc: Path | None = None,
    vite: Path | None = None,
    api_port: int = 19681,
    public_port: int = 19681,
) -> PreviewLaunchSpec | None:
    """Override, then original declaration; absence leaves detection unavailable.

    Only prepared person-owned dependencies are read. No installation, environment lookup,
    script execution or detection occurs here. Static-api uses one API/public listener.
    """
    override = values.get("OVERSIGHT_PREVIEW_APP")
    if "OVERSIGHT_PREVIEW_APP" in values and (not override or not Path(override).is_absolute()):
        raise ValueError(OVERRIDE_REFUSAL)
    path = Path(override) if override else original / "oversight.preview.json"
    if not override and not os.path.lexists(path):
        return None
    try:
        identity = FileIdentity.read(path)
        if identity.token[2] > 16384:
            raise ValueError(INVALID)
        with path.open("rb") as declaration_file:
            declaration_bytes = declaration_file.read(16385)
        if len(declaration_bytes) > 16384:
            raise ValueError(INVALID)
        raw: object = json.loads(declaration_bytes.decode("utf-8"), object_pairs_hook=_closed)
        identity.verify()
        if not isinstance(raw, dict):
            raise ValueError(INVALID)
        raw = cast(dict[str, object], raw)
        if set(raw) != {
            "version",
            "app",
            "mode",
            "build",
            "start",
            "readiness",
        }:
            raise ValueError(INVALID)
        if type(raw["version"]) is not int or raw["version"] != 1:
            raise ValueError(UNSUPPORTED)
        if raw["app"] != "oversight-self" or raw["mode"] != "static-api":
            raise ValueError(UNSUPPORTED)
        if raw["readiness"] != READINESS:
            raise ValueError(INVALID)
        if any(type(p) is not int or not 1 <= p <= 65535 for p in (api_port, public_port)):
            raise ValueError(INVALID)
        if api_port != public_port:
            raise ValueError(INVALID)
        resources = (python, node, tsc, vite)
        if any(p is None or not p.is_absolute() for p in resources):
            raise ValueError(INVALID)
        assert python is not None and node is not None and tsc is not None and vite is not None
        executables = (
            FileIdentity.read(python, executable=True),
            FileIdentity.read(node, executable=True),
            FileIdentity.read(tsc.resolve(strict=True)),
            FileIdentity.read(vite.resolve(strict=True)),
        )
        tree, state = tree or original, state or original / ".runtime/previews/id/state"
        _future_path(tree)
        _future_path(state)
        plain_path(original)
        plain_path(original / "frontend")
        substitutions = dict(
            zip(("python", "node", "tsc", "vite"), (str(p) for p in resources), strict=True)
        )
        substitutions |= {
            "T": str(tree),
            "state": str(state),
            "api_port": str(api_port),
            "public_port": str(public_port),
        }
        builds = raw["build"]
        if not isinstance(builds, list) or len(cast(list[object], builds)) != 2:
            raise ValueError(INVALID)
        build = tuple(
            _command(command, template, substitutions, tree)
            for command, template in zip(cast(list[object], builds), BUILD, strict=True)
        )
        start = _command(raw["start"], START, substitutions, tree)
        dependencies = tuple(
            FileIdentity.read(original / name)
            for name in ("uv.lock", "frontend/pnpm-lock.yaml", "frontend/package.json")
        )
        spec = PreviewLaunchSpec(
            "static-api",
            build,
            start,
            api_port,
            public_port,
            "person override" if override else "source declaration",
            identity,
            executables,
            dependencies,
            original,
            (python, node, tsc, vite),
            (
                READINESS["api"].format_map(substitutions),
                READINESS["public"].format_map(substitutions),
            ),
            state,
        )
        return replace(spec, proof=spec._signature())  # pyright: ignore[reportPrivateUsage]
    except (OSError, UnicodeError, ValueError, RuntimeError, KeyError, TypeError) as error:
        reason = str(error) if str(error) in {UNSUPPORTED, INVALID} else INVALID
        if override and isinstance(error, (OSError, UnicodeError)):
            reason = OVERRIDE_REFUSAL
        raise ValueError(reason) from None
