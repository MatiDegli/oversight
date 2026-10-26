"""Paths, deletion guards, copy, read, write, list and hash: blocking helpers, run by `offload`.

Every function here is synchronous on purpose. The workspace calls them through
`locks.offload`, so the event loop never waits on the disk.
"""

import contextlib
import errno
import hashlib
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from oversight.workspace.errors import InvalidPath, StaleFile, UnsafeDeletion, WorkspaceError

_AGENT_ID = re.compile(r"agent-[1-9][0-9]{0,3}")
MAX_PATH_CHARS = 1024
CACHE_DIRS = frozenset(
    {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv"}
)
FILE_MODE = 0o644
DIR_MODE = 0o755


@dataclass(frozen=True)
class FileRead:
    content: str
    hash: str
    """Lowercase hex SHA-256 of the exact bytes on disk."""


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def valid_agent_id(agent_id: str) -> bool:
    return _AGENT_ID.fullmatch(agent_id) is not None


# --- Paths ------------------------------------------------------------------------------------


def _refuse(path: str, reason: str) -> InvalidPath:
    """Name only a relative path: an absolute one would leak the workspace's location."""
    absolute = path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path) is not None
    shown = "<absolute>" if absolute else repr(path[:80])
    return InvalidPath(f"invalid path {shown}: {reason}")


def split_relative(path: str) -> list[str]:
    """Rules 1 to 3 of docs/plans/workspace.md, section 6: the shape of the path, no disk access."""
    if not path or len(path) >= MAX_PATH_CHARS:
        raise _refuse(path, "empty or too long")
    if "\x00" in path or "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise _refuse(path, "control character or backslash")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise _refuse(path, "must be relative to the workspace root")
    parts = path.split("/")
    if any(part in ("", ".", "..", ".git") for part in parts):
        raise _refuse(path, "empty, '.', '..' and '.git' segments are not allowed")
    return parts


def resolve_in(root: Path, path: str) -> Path:
    """Rules 4 and 5: no existing component is a symlink, and the real path stays under the root.

    Returns the lexical path under `root`, which equals the real one once both rules hold.
    """
    parts = split_relative(path)
    current = root
    for part in parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise _refuse(path, "a component is a symbolic link")
    real_root = os.path.realpath(root)
    real = os.path.realpath(root.joinpath(*parts))
    if os.path.commonpath([real_root, real]) != real_root:
        raise _refuse(path, "leaves the workspace")
    return root.joinpath(*parts)


# --- Read, write, list ------------------------------------------------------------------------


def _read_bytes(root: Path, path: str) -> bytes:
    target = resolve_in(root, path)
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise _refuse(path, "a component is a symbolic link") from error
        raise
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise _refuse(path, "not a regular file")
        return handle.read()


def read_file(root: Path, path: str) -> FileRead:
    data = _read_bytes(root, path)
    try:
        return FileRead(content=data.decode("utf-8"), hash=content_hash(data))
    except UnicodeDecodeError as error:
        raise WorkspaceError(f"not a UTF-8 text file: {path}") from error


def _make_parents(root: Path, path: str) -> None:
    """Create the missing directories one by one, checking each as it is made."""
    current = root
    for part in split_relative(path)[:-1]:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            os.mkdir(current, DIR_MODE)
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise _refuse(path, "a parent is not a directory")


def write_file(
    root: Path, tmp_dir: Path, path: str, content: str, expected_hash: str | None
) -> str:
    """Replace `path` with `content` if its current hash is `expected_hash`; return the new hash.

    `expected_hash=None` requires that the file not exist. The data goes to a temp file in
    `tmp_dir`, then `os.replace` swaps it in, so a reader never sees half a file.
    """
    data = content.encode("utf-8")
    _make_parents(root, path)
    target = resolve_in(root, path)
    mode = FILE_MODE
    current_hash: str | None = None
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode):
            raise _refuse(path, "not a regular file")
        mode = stat.S_IMODE(info.st_mode)
        current_hash = content_hash(_read_bytes(root, path))
    if current_hash != expected_hash:
        raise StaleFile(f"{path} changed since it was read" if current_hash else f"{path} exists")
    tmp = tmp_dir / f".write-{uuid.uuid4().hex}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchmod(handle.fileno(), mode)
        os.replace(tmp, resolve_in(root, path))
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
    return content_hash(data)


def list_files(root: Path) -> list[str]:
    """Regular files under `root`, sorted, as relative paths. Follows no link and skips `.git`,
    caches and `*.pyc`."""
    found: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in CACHE_DIRS:
                        pending.append(Path(entry.path))
                elif (
                    entry.is_file(follow_symlinks=False)
                    and entry.name != ".git"  # a worktree's .git is a file
                    and not entry.name.endswith(".pyc")
                ):
                    found.append(Path(entry.path).relative_to(root).as_posix())
    return sorted(found)


def copy_template(template: Path, destination: Path) -> None:
    """Copy file by file: refuse a symlink, skip git, caches and `*.pyc`, write files as 0644."""
    destination.mkdir(mode=DIR_MODE, exist_ok=True)
    with os.scandir(template) as entries:
        ordered = sorted(entries, key=lambda entry: entry.name)
    for entry in ordered:
        if entry.name in CACHE_DIRS or entry.name.endswith(".pyc"):
            continue
        if entry.is_symlink():
            raise WorkspaceError(f"the template contains a symbolic link: {entry.name}")
        target = destination / entry.name
        if entry.is_dir(follow_symlinks=False):
            copy_template(Path(entry.path), target)
        elif entry.is_file(follow_symlinks=False):
            shutil.copyfile(entry.path, target, follow_symlinks=False)
            os.chmod(target, FILE_MODE)
        else:
            raise WorkspaceError(f"the template contains a special file: {entry.name}")


# --- Deletion guards --------------------------------------------------------------------------


def _shown(root: Path, target: Path) -> str:
    try:
        return f".runtime/{target.relative_to(root)}"
    except ValueError:
        return "<outside the runtime root>"


def validate_runtime_root(root: Path) -> None:
    """The root must be an absolute, real path named `.runtime`, and never `/`, $HOME or above."""
    if not root.is_absolute() or root.name != ".runtime":
        raise UnsafeDeletion("the runtime root must be an absolute path named .runtime")
    if Path(os.path.realpath(root)) != root:
        raise UnsafeDeletion("the runtime root must be a real path, with no symbolic link in it")
    home = Path(os.path.realpath(os.path.expanduser("~")))
    if root == Path(root.anchor) or root == home or root in home.parents:
        raise UnsafeDeletion("the runtime root cannot be /, the home directory or above it")


def _is_deletable(root: Path, target: Path) -> bool:
    if target in (root / "workspace", root / "worktrees", root / "tmp", root / "demo-venv"):
        return True
    return target.parent == root / "worktrees" and valid_agent_id(target.name)


def remove_tree(root: Path, target: Path) -> None:
    """The only deletion in the product. A missing target is a no-op; a refusal raises before any
    filesystem call that could delete."""
    if not target.is_absolute() or not _is_deletable(root, target):
        raise UnsafeDeletion(f"not a deletable target: {_shown(root, target)}")
    if not os.path.lexists(target):
        return
    if target.is_symlink() or os.path.realpath(target) != str(target):
        raise UnsafeDeletion(f"a symbolic link in the way: {_shown(root, target)}")
    shutil.rmtree(target)


def registered_worktree(root: Path, main_root: Path, agent_id: str) -> Path:
    """The worktree directory git registered for `agent_id`, read from disk, or `UnsafeDeletion`.

    `.git/worktrees/{agent_id}/gitdir` must name `{target}/.git`, which must be a plain file that
    points back at that metadata directory. No git process runs.
    """
    if not valid_agent_id(agent_id):
        raise UnsafeDeletion(f"not an agent id: {agent_id!r}")
    target = root / "worktrees" / agent_id
    if not _is_deletable(root, target):
        raise UnsafeDeletion(f"not a deletable target: {_shown(root, target)}")
    if target.is_symlink() or os.path.realpath(target) != str(target) or not target.is_dir():
        raise UnsafeDeletion(f"not a plain directory: {_shown(root, target)}")
    metadata = main_root / ".git" / "worktrees" / agent_id
    dotgit = target / ".git"
    try:
        named = (metadata / "gitdir").read_text(encoding="utf-8").strip()
        pointer = dotgit.read_text(encoding="utf-8").strip() if not dotgit.is_symlink() else ""
    except OSError as error:
        raise UnsafeDeletion(f"{agent_id} is not a registered worktree") from error
    if named != str(dotgit) or pointer != f"gitdir: {metadata}":
        raise UnsafeDeletion(f"{agent_id} is not the worktree git registered for it")
    return target
