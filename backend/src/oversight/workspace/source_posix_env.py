"""Validate an existing POSIX venv and statically translate its editable imports.

No activation, installation, finder import or site initialization occurs here. A successful
result describes inputs for a future guarded launcher; it does not enforce that launcher.
In particular, ordinary Python startup would execute original .pth hooks and is unsuitable.
"""

import ast
import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from oversight.workspace import source_posix as posix
from oversight.workspace.locks import offload
from oversight.workspace.runner import Spawn, run_process

__all__ = [
    "EnvironmentWitness",
    "ImportPolicy",
    "OwnEnvironment",
    "prepare_environment",
    "verify_environment",
]

PHASE_SECONDS = 10
MAX_METADATA = 4 * 1024 * 1024
MAX_INTERPRETER = 64 * 1024 * 1024
PROBE = (
    "import json, os, sys, sysconfig; print(json.dumps(dict("
    "version=list(sys.version_info[:3]), base=os.path.realpath(sys._base_executable), "
    "prefix=sys.base_prefix, stdlib=sysconfig.get_path('stdlib'))))"
)


def refuse(reason: str) -> OSError:
    return OSError("Own-source POSIX environment unavailable: " + reason)


@dataclass(frozen=True)
class EnvironmentWitness:
    path: Path
    identity: tuple[int, ...]
    sha256: str | None = None
    link: str | None = None


@dataclass(frozen=True)
class ImportPolicy:
    source: Path
    tree: Path
    libraries: tuple[Path, ...]
    packages: tuple[str, ...]
    isolated: bool = True
    no_site: bool = True
    no_bytecode: bool = True


@dataclass(frozen=True)
class OwnEnvironment:
    source: posix.PosixSource
    python: Path
    base_python: Path
    version: tuple[int, int, int]
    roots: tuple[Path, ...]
    policy: ImportPolicy
    witnesses: tuple[EnvironmentWitness, ...]


@dataclass
class _IO:
    deadline: float
    site: Path
    witnesses: dict[Path, EnvironmentWitness] = field(
        default_factory=lambda: dict[Path, EnvironmentWitness]()
    )
    consumed: int = 0
    visited: int = 0

    def remember(self, witness: EnvironmentWitness) -> None:
        posix.remaining(self.deadline)
        old = self.witnesses.get(witness.path)
        if old is not None and old.identity[:5] == witness.identity[:5]:
            if len(witness.identity) == 5:
                return
            if len(old.identity) == 5:
                old = None
        if old is not None and old != witness:
            raise refuse("environment/input changed during validation")
        self.witnesses[witness.path] = witness
        if len(self.witnesses) > posix.MAX_PATHS:
            raise refuse("environment witness budget exceeded")

    def directory(self, path: Path) -> None:
        with posix.directory(path):
            posix.secure(path.lstat())
            self.remember(EnvironmentWitness(path, posix.token(path.lstat())))

    def read(self, path: Path) -> bytes:
        data, stamp = posix.read_file(
            path, self.deadline, hardlinks=(path.is_relative_to(self.site) and path != self.site)
        )
        self.consumed += len(data)
        if self.consumed > MAX_METADATA:
            raise refuse("editable metadata byte budget exceeded")
        self.remember(EnvironmentWitness(path, stamp, hashlib.sha256(data).hexdigest()))
        return data

    def children(self, path: Path) -> tuple[Path, ...]:
        self.directory(path)
        with posix.directory(path) as fd:
            names = posix.directory_names(fd, self.deadline)
        self.visited += len(names)
        if self.visited > posix.MAX_PATHS:
            raise refuse("environment directory entry budget exceeded")
        return tuple(path / n for n in sorted(names))

    def resolve_interpreter(self, path: Path, *, directory_only: bool = False) -> tuple[Path, str]:
        """Resolve at most 32 ordinary symlinks, validating every traversed parent."""
        posix.absolute(path)
        seen: set[tuple[int, ...]] = set()
        hops = 0
        pending = list(path.parts[1:])
        current = Path("/")
        while pending:
            posix.remaining(self.deadline)
            part = pending.pop(0)
            if part in {"", "."}:
                continue
            if part == "..":
                current = current.parent
                continue
            posix.relative(part)
            target = current / part
            with posix.directory(current) as fd:
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    stamp = posix.token(info)
                    if (
                        stamp[:2] in seen
                        or hops >= 32
                        or info.st_uid not in {posix.ROOT_UID, os.geteuid()}
                    ):
                        raise refuse("unsafe interpreter link or cycle")
                    seen.add(stamp[:2])
                    hops += 1
                    link = os.readlink(part, dir_fd=fd)
                    if posix.token(os.stat(part, dir_fd=fd, follow_symlinks=False)) != stamp:
                        raise refuse("interpreter link changed")
                    self.remember(EnvironmentWitness(target, stamp, link=link))
                    if link.startswith("//") or len(link.encode()) > 4096:
                        raise refuse("unsafe interpreter link target")
                    if link.startswith("/"):
                        current = Path("/")
                    pending = [*link.split("/"), *pending]
                    if len(pending) > 128:
                        raise refuse("interpreter link component budget exceeded")
                    continue
                posix.secure(info, sticky_ancestor=bool(pending))
                if pending:
                    if not stat.S_ISDIR(info.st_mode):
                        raise refuse("unsafe interpreter ancestor")
                    # Stable ancestor identities, without rejecting unrelated sibling writes.
                    self.remember(EnvironmentWitness(target, posix.identity(info)))
                elif not directory_only:
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_mode & 0o111 == 0
                        or (info.st_mode & (stat.S_ISUID | stat.S_ISGID))
                    ):
                        raise refuse("unsafe base interpreter")
            current = target
        if directory_only:
            self.directory(current)
            return current, ""
        data, stamp = posix.read_file(current, self.deadline, limit=MAX_INTERPRETER)
        if not data.startswith(b"\x7fELF"):
            raise refuse("unsupported interpreter executable")
        digest = hashlib.sha256(data).hexdigest()
        self.remember(EnvironmentWitness(current, stamp, digest))
        return current, digest


def _config(io: _IO, source: posix.PosixSource) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in io.read(source.root / ".venv/pyvenv.cfg").decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip().lower()
        if not sep or not key or key in values:
            raise refuse("malformed or duplicate venv configuration")
        values[key] = value.strip()
    if values.get("include-system-site-packages", "").lower() != "false":
        raise refuse("system site-packages are unsupported")
    if values.get("implementation", "CPython") != "CPython":
        raise refuse("unsupported interpreter implementation")
    return values


@dataclass(frozen=True)
class _Base:
    python: Path
    base: Path
    version: tuple[int, int, int]
    home: Path


def _base(source: posix.PosixSource, tree: Path, io: _IO) -> _Base:
    posix.recheck_source(source)
    posix.absolute(tree)
    posix.recheck_tree(source, tree)
    for path in (tree, source.root / ".venv", source.root / ".venv/bin"):
        io.directory(path)
    tree_inode = posix.identity(tree.lstat())[:2]
    if tree_inode in {posix.identity(p.lstat())[:2] for p in (source.root, source.ide)}:
        raise refuse("aliased disposable tree")
    values = _config(io, source)
    raw_version = values.get("version", values.get("version_info", ""))
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\..*)?", raw_version)
    if match is None:
        raise refuse("missing interpreter version")
    version = tuple(int(match[i]) for i in (1, 2, 3))
    if version[:2] != (3, 12):
        raise refuse("only CPython 3.12 environments are supported")
    home, _ = io.resolve_interpreter(posix.absolute(Path(values["home"])), directory_only=True)
    base, base_hash = io.resolve_interpreter(home / f"python{version[0]}.{version[1]}")
    if any(base.is_relative_to(p) for p in (source.root, source.runtime, source.ide)):
        raise refuse("base interpreter overlaps selected or runtime code")
    if "executable" in values:
        configured, _ = io.resolve_interpreter(posix.absolute(Path(values["executable"])))
        if configured != base:
            raise refuse("inconsistent configured base executable")
    python = source.root / ".venv/bin/python"
    resolved, digest = io.resolve_interpreter(python)
    if resolved != base and (resolved != python or digest != base_hash):
        raise refuse("venv interpreter does not match configured base")
    io.directory(io.site)
    return _Base(python, base, cast(tuple[int, int, int], version), home)


def _package(name: str) -> str:
    if not name or any(not part.isidentifier() for part in name.split(".")):
        raise refuse("invalid editable package name")
    return name


def _mapped(raw: str, source: posix.PosixSource, tree: Path, io: _IO) -> Path:
    if not raw or "\x00" in raw:
        raise refuse("empty or invalid editable target")
    raw_path = Path(raw if raw.startswith("/") else io.site / raw)
    original = Path(os.path.normpath(raw_path))
    posix.absolute(original)
    if not original.is_relative_to(source.root) or original.is_relative_to(source.root / ".venv"):
        raise refuse("editable target escapes original project")
    # Do not erase a symlink with lexical '..' normalization: POSIX resolves the
    # link before its parent component. Every raw traversed ancestor must be plain.
    current = Path("/")
    for index, part in enumerate(raw_path.parts[1:]):
        if part == "..":
            current = current.parent
            continue
        posix.relative(part)
        with posix.directory(current) as fd:
            info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or (
                index < len(raw_path.parts) - 2 and not stat.S_ISDIR(info.st_mode)
            ):
                raise refuse("linked or non-directory editable ancestor")
        current /= part
    rel = original.relative_to(source.root)
    if rel.parts and not posix.selected(rel.as_posix()):
        raise refuse("editable target is excluded source state")
    mapped = tree / rel
    for path in (original, mapped):
        if path.is_dir():
            io.directory(path)
        else:
            io.read(path)
    return mapped


def _names(root: Path, io: _IO) -> set[str]:
    names: set[str] = set()
    for path in io.children(root):
        if path.name.startswith(".") or path.name == "__pycache__":
            continue
        if path.is_dir():
            io.directory(path)
            names.add(_package(path.name))
        elif path.suffix == ".py":
            io.read(path)
            names.add(_package(path.stem))
    return names


def _finder(data: bytes) -> dict[str, dict[str, Any]]:
    literals: dict[str, dict[str, Any]] = {}
    for statement in ast.parse(data).body:
        if not isinstance(statement, ast.Assign | ast.AnnAssign) or statement.value is None:
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"MAPPING", "NAMESPACES"}:
                value: object = ast.literal_eval(statement.value)
                if target.id in literals or not isinstance(value, dict):
                    raise refuse("invalid finder literal")
                literals[target.id] = cast(dict[str, Any], value)
    if "MAPPING" not in literals:
        raise refuse("missing finder mapping")
    return literals


@dataclass(frozen=True)
class _Distribution:
    project: Path
    names: frozenset[str]
    pth: frozenset[str]


def _distribution(
    path: Path, source: posix.PosixSource, tree: Path, io: _IO
) -> _Distribution | None:
    io.directory(path)
    url = path / "direct_url.json"
    if not os.path.lexists(url):
        return None
    raw: object = json.loads(io.read(url))
    if not isinstance(raw, dict):
        raise refuse("malformed distribution URL metadata")
    data = cast(dict[str, Any], raw)
    editable = data.get("dir_info", {}).get("editable") is True
    if not isinstance(data.get("url"), str):
        raise refuse("malformed distribution URL")
    parsed = urlsplit(str(data["url"]))
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or (parsed.query or parsed.fragment)
    ):
        if editable:
            raise refuse("unsupported editable URL")
        return None
    original = Path(unquote(parsed.path))
    if not original.is_relative_to(source.root):
        if editable:
            raise refuse("editable distribution outside selected project")
        return None
    project = _mapped(str(original), source, tree, io)
    names = (
        frozenset(_package(n) for n in io.read(path / "top_level.txt").decode().splitlines() if n)
        if os.path.lexists(path / "top_level.txt")
        else frozenset[str]()
    )
    records = io.read(path / "RECORD").decode().splitlines()
    pth: set[str] = set()
    for row in records:
        name = row.split(",", 1)[0]
        if name.endswith(".pth") and "/" not in name and "\\" not in name:
            posix.relative(name)
            pth.add(name)
    if not pth:
        raise refuse("unassociated editable distribution")
    if not editable and not names:
        raise refuse("installed original project has no package names")
    return _Distribution(project, names, frozenset(pth))


def _path_mapping(
    path: Path, source: posix.PosixSource, tree: Path, io: _IO
) -> tuple[set[Path], set[str]]:
    roots: set[Path] = set()
    packages: set[str] = set()
    for line in io.read(path).decode("utf-8-sig").splitlines():
        if not line or line.startswith("#"):
            continue
        # uv's bootstrap hook is unnecessary with -S and is deliberately never executed.
        # It carries no project mapping; RECORD association below still applies to editables.
        if path.name == "_virtualenv.pth" and line == "import _virtualenv":
            continue
        if not line.startswith(("import ", "import\t")):
            root = _mapped(line, source, tree, io)
            io.directory(root)
            roots.add(root)
            packages.update(_names(root, io))
            continue
        match = re.fullmatch(r"import (__editable__\w+);\s*\1\.install\(\)", line)
        if match is None:
            raise refuse("unsupported executable .pth hook")
        finder = _finder(io.read(io.site / (match[1] + ".py")))
        for kind, mapping in finder.items():
            for name, value in mapping.items():
                _package(name)
                values: object = [value] if kind == "MAPPING" else value
                if (
                    not isinstance(values, list)
                    or not values
                    or any(not isinstance(v, str) for v in cast(list[object], values))
                ):
                    raise refuse("unsupported finder target")
                for raw in cast(list[str], values):
                    mapped = _mapped(raw, source, tree, io)
                    parts = name.split(".")
                    package_path = mapped.with_suffix("") if mapped.suffix == ".py" else mapped
                    if tuple(package_path.parts[-len(parts) :]) != tuple(parts):
                        raise refuse("finder target cannot be represented by import roots")
                    root = package_path.parents[len(parts) - 1]
                    if not root.is_relative_to(tree):
                        raise refuse("finder root escapes disposable tree")
                    io.directory(root)
                    roots.add(root)
                    packages.add(name)
    return roots, packages


def _editable_imports(
    source: posix.PosixSource, tree: Path, io: _IO
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Bounded static metadata, with RECORD association for every translated mapping."""
    children = io.children(io.site)
    distributions = [
        distribution
        for path in children
        if path.name.endswith(".dist-info")
        if (distribution := _distribution(path, source, tree, io)) is not None
    ]
    mappings: dict[str, tuple[set[Path], set[str]]] = {}
    for path in children:
        if path.suffix == ".egg-link":
            raise refuse("legacy editable metadata is unsupported")
        if path.suffix == ".pth":
            mappings[path.name] = _path_mapping(path, source, tree, io)
    associated: set[str] = set()
    roots: set[Path] = set()
    packages: set[str] = set()
    for distribution in distributions:
        local_roots: set[Path] = set()
        local_packages: set[str] = set()
        for name in distribution.pth:
            if name not in mappings:
                raise refuse("distribution mapping is missing")
            mapped_roots, mapped_packages = mappings[name]
            if not mapped_roots or any(
                not p.is_relative_to(distribution.project) for p in mapped_roots
            ):
                raise refuse("unassociated editable mapping")
            local_roots.update(mapped_roots)
            local_packages.update(mapped_packages)
            associated.add(name)
        if not local_packages or not distribution.names.issubset(local_packages):
            raise refuse("incomplete editable package mapping")
        roots.update(local_roots)
        packages.update(local_packages)
    if any(name not in associated and mapped[0] for name, mapped in mappings.items()):
        raise refuse("unassociated path/finder metadata")
    return tuple(sorted(roots)), tuple(sorted(packages))


def _verify(witnesses: tuple[EnvironmentWitness, ...], deadline: float) -> None:
    for witness in witnesses:
        posix.remaining(deadline)
        with posix.directory(witness.path.parent) as fd:
            info = os.stat(witness.path.name, dir_fd=fd, follow_symlinks=False)
            stamp = posix.identity(info) if len(witness.identity) == 5 else posix.token(info)
            if stamp != witness.identity:
                raise refuse("validated environment identity changed")
            if witness.link is not None:
                if os.readlink(witness.path.name, dir_fd=fd) != witness.link:
                    raise refuse("validated interpreter link changed")
            elif witness.sha256 is not None:
                data, _ = posix.read_file(
                    witness.path, deadline, limit=MAX_INTERPRETER, hardlinks=True
                )
                if hashlib.sha256(data).hexdigest() != witness.sha256:
                    raise refuse("validated environment bytes changed")


async def verify_environment(environment: OwnEnvironment, *, deadline: float | None = None) -> None:
    """Recheck input witnesses before every launch; no execution or writes."""
    until = (
        min(deadline, time.monotonic() + PHASE_SECONDS)
        if deadline is not None
        else (time.monotonic() + PHASE_SECONDS)
    )
    await offload(lambda: posix.recheck_source(environment.source))
    await offload(lambda: _verify(environment.witnesses, until))


async def prepare_environment(
    source: posix.PosixSource,
    tree: Path,
    *,
    spawn: Spawn = run_process,
    deadline: float | None = None,
) -> OwnEnvironment:
    """Validate only original .venv/bin/python; no fallback or dependency changes.

    Supports CPython 3.12, ordinary POSIX venv interpreter links or a byte-identical copy,
    path-line editables and setuptools literal MAPPING/NAMESPACES finders. Unsupported .pth
    code refuses, even when unrelated. Consumers must skip site initialization, translate
    roots and enforce policy with a POSIX-aware import guard before executing project code.
    """
    until = (
        min(deadline, time.monotonic() + PHASE_SECONDS)
        if deadline is not None
        else (time.monotonic() + PHASE_SECONDS)
    )
    io = _IO(until, source.root / ".venv/lib/python3.12/site-packages")
    try:
        base = await offload(lambda: _base(source, tree, io))
        result = await spawn(
            [str(base.python), "-I", "-S", "-B", "-c", PROBE],
            cwd=tree,
            timeout_s=posix.remaining(until),
            output_cap=16384,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
        )
        posix.remaining(until)
        if (
            result.returncode != 0
            or result.timed_out
            or result.truncated
            or len(result.stdout) > 16384
        ):
            raise refuse("failed, timed-out or truncated interpreter probe")
        data: object = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise refuse("malformed interpreter probe")
        probe = cast(dict[str, Any], data)
        if probe["version"] != list(base.version) or probe["base"] != str(base.base):
            raise refuse("inconsistent interpreter/base configuration")
        prefix, _ = await offload(
            lambda: io.resolve_interpreter(
                posix.absolute(Path(probe["prefix"])), directory_only=True
            )
        )
        stdlib, _ = await offload(
            lambda: io.resolve_interpreter(
                posix.absolute(Path(probe["stdlib"])), directory_only=True
            )
        )
        if prefix != base.home.parent or not stdlib.is_relative_to(prefix):
            raise refuse("stdlib outside validated base")
        roots, packages = await offload(lambda: _editable_imports(source, tree, io))
        policy = ImportPolicy(source.root, tree, (stdlib, io.site), packages)
        environment = OwnEnvironment(
            source,
            base.python,
            base.base,
            base.version,
            roots,
            policy,
            tuple(io.witnesses[p] for p in sorted(io.witnesses)),
        )
        await verify_environment(environment, deadline=until)
        return environment
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        RecursionError,
        SyntaxError,
    ):
        raise refuse("missing, changed or unsupported interpreter/editable metadata") from None
