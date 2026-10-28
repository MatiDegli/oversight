"""Bounded private worker protocol; callers retain tree locks and the Git lease."""

import json
import os
import stat
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from oversight.workspace.errors import WorkspaceError
from oversight.workspace.integrity import IntegrityError
from oversight.workspace.isolation import IsolatedRunner
from oversight.workspace.locks import offload
from oversight.workspace.runner import Spawn
from oversight.workspace.snapshot import finish_snapshot, take_snapshot

if TYPE_CHECKING:
    from oversight.workspace.core import Tree, Workspace

MAX_BEHAVIOR_FILE = 1_048_576
PER_CALL_TIMEOUT_S = 0.2
WORKER = Path(__file__).with_name("behavior_worker.py")
type TypedResult = dict[str, Any]


@dataclass(frozen=True)
class BehaviorTarget:
    id: str
    module: str
    name: str
    calls: tuple[str, ...]


@dataclass(frozen=True)
class BehaviorObservation:
    results: tuple[TypedResult, ...] = ()
    problem: str | None = None


def _read_report(path: Path, targets: Sequence[BehaviorTarget]) -> dict[str, BehaviorObservation]:
    # A worker has filesystem access; refuse links/devices and limit the actual read too.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise WorkspaceError("behavior report is not a plain file")
        raw = handle.read(MAX_BEHAVIOR_FILE + 1)
    if len(raw) > MAX_BEHAVIOR_FILE:
        raise WorkspaceError("behavior report exceeds size cap")
    parsed: Any = json.loads(raw)
    if not isinstance(parsed, dict):
        raise WorkspaceError("invalid behavior report")
    parsed = cast(dict[str, Any], parsed)
    found: dict[str, BehaviorObservation] = {}
    for target in targets:
        row = parsed.get(target.id)
        if row is None:
            found[target.id] = BehaviorObservation(problem="budget exhausted")
            continue
        if not isinstance(row, dict):
            raise WorkspaceError("invalid behavior observation")
        row = cast(dict[str, Any], row)
        if row.get("problem") not in (None, "import failed"):
            raise WorkspaceError("invalid behavior observation")
        results = row.get("results")
        if not isinstance(results, list):
            raise WorkspaceError("invalid behavior results")
        results = cast(list[Any], results)
        if len(results) > len(target.calls):
            raise WorkspaceError("invalid behavior results")
        if row["problem"] is None and len(results) != len(target.calls):
            raise WorkspaceError("incomplete behavior results")
        if any(
            not isinstance(result, dict)
            or cast(dict[str, Any], result).get("kind")
            not in ("value", "raised", "timed_out", "unrepresentable")
            for result in results
        ):
            raise WorkspaceError("invalid typed behavior result")
        found[target.id] = BehaviorObservation(tuple(results), row["problem"])
    return found


async def run_behavior_worker(
    workspace: "Workspace",
    tree: "Tree",
    *,
    python: Path,
    targets: Sequence[BehaviorTarget],
    timeout_s: float,
    spawn: Spawn,
) -> dict[str, BehaviorObservation]:
    """C removes the network only. Checkout disposal contains ordinary file effects.

    Snapshot/restore Git metadata because detached worktrees still share .git with main.
    Cleanup and verified restoration finish before locks are released on cancellation.
    """
    stem = workspace.tmp_dir / f"behavior-{uuid.uuid4().hex}"
    request, report = stem.with_suffix(".input"), stem.with_suffix(".json")
    raw = json.dumps([asdict(target) for target in targets])
    if len(raw.encode()) > MAX_BEHAVIOR_FILE:
        return {target.id: BehaviorObservation(problem="budget exhausted") for target in targets}
    snapshot = await offload(
        partial(take_snapshot, tree.git_identity, workspace.integrity, timeout_s)
    )
    try:
        await offload(lambda: request.write_text(raw, encoding="utf-8"))
        process = await spawn(
            [
                str(python),
                "-B",
                str(WORKER),
                str(tree.root),
                str(request),
                str(report),
                str(PER_CALL_TIMEOUT_S),
            ],
            cwd=tree.root,
            timeout_s=timeout_s,
            env={
                "HOME": str(workspace.tmp_dir / "home"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
            output_cap=1024,
        )
        if process.timed_out:
            raise TimeoutError("behavior budget exhausted")
        if process.returncode != 0:
            raise WorkspaceError("behavior worker failed")
        return await offload(partial(_read_report, report, targets))
    finally:
        await offload(lambda: (request.unlink(missing_ok=True), report.unlink(missing_ok=True)))
        if isinstance(spawn, IsolatedRunner) and not spawn.cleanup_complete:
            error = IntegrityError(
                "behavior namespace cleanup is unverified", ".git", code="main_suspect"
            )
            workspace.integrity.suspend(error, uncertain=True)
            raise error
        await offload(partial(finish_snapshot, snapshot, workspace.integrity))
