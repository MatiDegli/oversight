"""Ordinary suite artifacts veto that execution's evidence, never session authority."""

import hashlib
import os
import stat
import sys
import time
from pathlib import Path

from oversight.workspace.integrity import IntegrityError, remaining
from oversight.workspace.metadata_io import (
    MAX_ENTRIES,
    Budget,
    directory,
    nt_guard,
    nt_name,
    read_at,
)
from oversight.workspace.snapshot import GUARD_SECONDS

type Stamp = dict[str, tuple[int, bytes]]


def file_stamp(root: Path) -> Stamp:
    """Include ignored artifacts and link text, without traversing links or nested Git."""
    budget = Budget(time.monotonic() + GUARD_SECONDS)
    if sys.platform == "win32":
        nt_guard(root, budget.deadline, working=True)
    result: Stamp = {}
    pending = [root]
    while pending:
        path = pending.pop()
        with directory(path, budget.deadline) as fd, os.scandir(fd) as children:
            for child in children:
                remaining(budget.deadline)
                name = child.name
                if sys.platform == "win32":
                    if path == root and (name.casefold() == ".git" or name.upper() == ".GIT"):
                        continue
                    nt_name(name, working=True)
                if path == root and name == ".git":
                    continue
                key = (path / name).relative_to(root).as_posix()
                info = (
                    nt_guard(path / name, budget.deadline, working=True)
                    if sys.platform == "win32"
                    else child.stat(follow_symlinks=False)
                )
                if sys.platform != "win32" and stat.S_ISLNK(info.st_mode):
                    assert isinstance(fd, int)
                    data = os.fsencode(os.readlink(name, dir_fd=fd))
                    budget.charge(len(data))
                else:
                    entry = read_at(fd, name, path / name, budget, kind="working file")
                    if entry is None:
                        raise IntegrityError("suite file disappeared during comparison", key)
                    data = hashlib.sha256(entry.data).digest() if entry.data is not None else b""
                    if entry.data is None and name.casefold() != ".git":
                        pending.append(path / name)
                result[key] = (info.st_mode, data)
                if len(result) > MAX_ENTRIES:
                    raise IntegrityError("suite files exceed comparison budget", ".")
    return result


def file_problems(root: Path, before: Stamp) -> tuple[str, ...]:
    try:
        after = file_stamp(root)
        changed = next(
            (key for key in before.keys() | after.keys() if before.get(key) != after.get(key)), None
        )
        if changed is None:
            return ()
        error = IntegrityError(
            "suite changed working files or left an unexpected artifact", changed
        )
    except (OSError, IntegrityError) as cause:
        error = IntegrityError("suite working files could not be verified", type(cause).__name__)
    return (str(error),)
