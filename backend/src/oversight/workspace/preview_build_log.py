"""Supervisor-only, replace-on-write storage for complete captured build diagnostics."""

import contextlib
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

MAX_BYTES = 1024 * 1024
_ESCAPES = re.compile(
    r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)"
    r"|(?:\x1b[P^_]|[\x90\x9e\x9f]).*?(?:\x1b\\|\x9c|$)"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
    r"|\x1b[ -/]*[@-~]",
    re.DOTALL,
)


def _inert(text: str) -> str:
    text = _ESCAPES.sub("", text)
    return "".join(c for c in text if c == "\n" or not unicodedata.category(c).startswith("C"))


def _bounded(text: str, limit: int = MAX_BYTES) -> bytes:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return data
    # Reserve a fixed bound for the marker; slicing must never split a UTF-8 code point.
    if limit <= 96:
        return data[:limit].decode("utf-8", errors="ignore").encode()
    room = limit - 96
    head = data[: room // 2].decode("utf-8", errors="ignore").encode()
    tail = data[-(room // 2) :].decode("utf-8", errors="ignore").encode()
    marker = f"\n[... {len(data) - len(head) - len(tail)} bytes omitted ...]\n".encode()
    return head + marker + tail


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    """Repeat because removing one secret can join text into another secret."""
    while True:
        before = text
        for secret in secrets:
            text = text.replace(secret, "")
        if text == before:
            return text


def _directories(path: Path) -> tuple[int, tuple[tuple[int, int], ...]]:
    """Walk without following any link, retaining an fd to the actual log directory."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    identities: list[tuple[int, int]] = []
    try:
        for component in path.parts[1:]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            identities.append((info.st_dev, info.st_ino))
        return fd, tuple(identities)
    except BaseException:
        os.close(fd)
        raise


def _safe_destination(fd: int) -> None:
    try:
        info = os.stat("build.log", dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise ValueError("unsafe build log replacement")


@dataclass(frozen=True)
class SanitizedBuildLog:
    """Pin an existing private logs directory; reject later directory/link replacement.

    Each write replaces the previous complete capture. The sink creates no lifecycle events.
    Parent directories must have been established by the trusted supervisor before construction.
    """

    path: Path
    secrets: tuple[str, ...] = ()
    max_bytes: int = MAX_BYTES
    _identities: tuple[tuple[int, int], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or self.max_bytes <= 0:
            raise ValueError("invalid build diagnostic budget")
        path = Path(self.path)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.name != "build.log"
            or path.parent.name != "logs"
            or path.parents[2].name != "previews"
        ):
            raise ValueError("build log must be an absolute previews/<id>/logs/build.log path")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "secrets", tuple(self.secrets))
        fd, identities = _directories(path.parent)
        try:
            _safe_destination(fd)
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError("build log directory must be supervisor-owned and private")
        finally:
            os.close(fd)
        object.__setattr__(self, "_identities", identities)

    def write(self, output: str) -> None:
        """Sanitize before truncation; atomically replace within the frozen UTF-8 budget."""
        secrets = tuple(sorted({s for s in self.secrets if s}, key=len, reverse=True))
        output = _inert(_redact(output, secrets))
        normalized = tuple(
            sorted(set(secrets) | {_inert(s) for s in secrets} - {""}, key=len, reverse=True)
        )
        output = _redact(output, normalized)
        # The generated omission marker must also obey redaction.
        data = _redact(_bounded(output, self.max_bytes).decode(), normalized).encode()
        fd, identities = _directories(self.path.parent)
        temporary = ".build-" + uuid4().hex
        try:
            if identities != self._identities:
                raise ValueError("build log directory identity changed")
            _safe_destination(fd)
            out = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(out, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            check_fd, check_identities = _directories(self.path.parent)
            os.close(check_fd)
            if check_identities != identities:
                raise ValueError("build log directory identity changed")
            _safe_destination(fd)
            os.replace(temporary, "build.log", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=fd)
            os.close(fd)
