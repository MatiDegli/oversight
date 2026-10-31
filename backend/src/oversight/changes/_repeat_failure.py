"""Pure guard against spending an automatic round on the same untouched failure."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from oversight.events import absolute_paths
from oversight.graph import parse_version, test_reaches
from oversight_contracts.events import (
    Limits,
    ServerEvent,
    SuiteResult,
    TestsExecutedEvent,
)
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.review import ChangeTarget


@dataclass(frozen=True)
class RepeatVerdict:
    outcome: Literal["clear", "unchanged", "unknown"]
    reason: str
    test_ids: tuple[str, ...] = ()


def _verdict(
    outcome: Literal["clear", "unchanged", "unknown"], reason: str, ids: Sequence[str] = ()
) -> RepeatVerdict:
    names = ", ".join(i[:79] + "…" if len(i) > 80 else i for i in ids[:3])
    if len(ids) > 3:
        names += f" +{len(ids) - 3} more"
    if absolute_paths(names):
        names = ""  # Never repeat an absolute path from an untrusted test id.
    suffix = f": {names}" if names else ""
    bound = 400 - len(suffix)
    text = reason[: bound - 1] + "…" if len(reason) > bound else reason
    return RepeatVerdict(outcome, text + suffix, tuple(ids))


def repeat_verdict(
    events: Sequence[ServerEvent], target: ChangeTarget, graph: GraphSnapshot
) -> RepeatVerdict:
    """Read committed evidence only; malformed or incomplete logs always fail closed."""
    try:
        return _compare(sorted(events, key=lambda e: e.seq), target, graph)
    except Exception:
        # Also accept odd legacy/constructed logs without exposing their contents.
        return _verdict("unknown", "Failure comparison evidence could not be read.")


def _compare(
    events: Sequence[ServerEvent], target: ChangeTarget, graph: GraphSnapshot
) -> RepeatVerdict:
    if any(
        e.type == "change.resolved"
        and e.payload.change_id == target.change_id
        and e.payload.decided_by == "cross_review"
        for e in events
    ):
        return _verdict("clear", "The existing automatic round check takes precedence.")
    limits = next((e.payload.limits for e in reversed(events) if e.type == "run.started"), None)
    if limits is None:
        return _verdict("unknown", "Run limits are unavailable.")
    runs = [
        e for e in events if e.type == "tests.executed" and e.payload.change_id == target.change_id
    ]
    current = next(
        (
            e
            for e in reversed(runs)
            if e.payload.phase == "proposal" and e.payload.revision == target.revision
        ),
        None,
    )
    if current is None:
        return _verdict("unknown", "The current proposal test run is unavailable.")
    last = next(
        (
            e
            for e in reversed(runs)
            if e.seq < current.seq and e.payload.phase in {"agent", "proposal"}
        ),
        None,
    )
    if last is None:
        return _verdict("clear", "There is no earlier run.")
    before, after = last.payload.suite, current.payload.suite
    if "passed" in {before.outcome, after.outcome} or before.outcome != after.outcome:
        return _verdict("clear", "The test outcome changed or a run passed.")
    if after.outcome != "failed":
        return _verdict("unknown", "Errors and timeouts cannot establish the same assertion.")
    if any(s.cases is None or s.cases_complete is not True for s in (before, after)):
        return _verdict("unknown", "Complete per-test results are unavailable.")
    old, new = _failures(before), _failures(after)
    ids = tuple(sorted(new))
    if old.keys() != new.keys():
        return _verdict("clear", "The failing test ids changed.", ids)
    if any(old[k] is not None and new[k] is not None and old[k] != new[k] for k in new):
        return _verdict("clear", "The failing assertion changed.", ids)
    causes: list[str] = []
    if not new or any(not old[k] or not new[k] for k in new):
        causes.append("Assertion detail is unavailable")
    if _base_moved(events, target, last):
        causes.append("The change base moved since the earlier run")
    changed = _changed_nodes(events, target, last, current, causes)
    return _reach_verdict(graph, changed, ids, limits, causes)


def _reach_verdict(
    graph: GraphSnapshot,
    changed: set[str],
    ids: tuple[str, ...],
    limits: Limits,
    causes: list[str],
) -> RepeatVerdict:
    if graph.parse_errors:
        causes.append("The change graph has parse errors")
    for test_id in ids:
        roots = [
            n.id
            for n in graph.nodes
            if n.is_test
            and n.kind in {"function", "method"}
            and "#" not in n.qualname
            and "::".join([n.path, *n.qualname.split(".")]) == test_id.split("[", 1)[0]
        ]
        reach = (
            test_reaches(
                graph,
                roots[0],
                changed,
                max_depth=limits.cone_max_depth,
                max_nodes=limits.cone_max_nodes,
            )
            if len(roots) == 1
            else None
        )
        if reach is True:
            return _verdict("clear", "An edit since the last run reaches the test", (test_id,))
        if reach is None:
            causes.append("A failing test's exact call reach is unknown")
    if causes:
        return _verdict("unknown", "; ".join(dict.fromkeys(causes)), ids)
    return _verdict("unchanged", "The same failure has no edit reached by its test", ids)


def _failures(suite: SuiteResult) -> dict[str, str | None]:
    return {c.test_id: c.detail for c in suite.cases or () if c.outcome in {"failed", "error"}}


def _base_moved(
    events: Sequence[ServerEvent], target: ChangeTarget, last: TestsExecutedEvent
) -> bool:
    for event in reversed(events):
        if event.seq > last.seq:
            continue
        if event.type == "change.proposed" and event.payload.change_id == target.change_id:
            return event.payload.base_commit != target.base_commit
        if (
            event.type == "agent.status"
            and event.payload.agent_id == last.payload.agent_id
            and event.payload.base_commit is not None
        ):
            return event.payload.base_commit != target.base_commit
    return False


def _changed_nodes(
    events: Sequence[ServerEvent],
    target: ChangeTarget,
    last: TestsExecutedEvent,
    current: TestsExecutedEvent,
    causes: list[str],
) -> set[str]:
    if last.payload.tested_tree == current.payload.tested_tree:
        return set()
    edits = [
        e
        for e in events
        if e.type == "change.updated"
        and e.payload.change_id == target.change_id
        and e.seq < current.seq
    ]
    paths = sorted({e.payload.file.path for e in edits if last.seq < e.seq})
    if not paths:
        causes.append("Tested trees differ without recorded edits")
    changed: set[str] = set()
    for path in paths:
        history = [e for e in edits if e.payload.file.path == path]
        previous = [e for e in history if e.seq < last.seq]
        following = [e for e in history if last.seq < e.seq]
        before = (
            previous[-1].payload.file.modified if previous else following[0].payload.file.original
        )
        after = history[-1].payload.file.modified
        if before == after:
            continue
        _file_changes(path, before, after, changed, causes)
    return changed


def _file_changes(
    path: str, before: str | None, after: str | None, changed: set[str], causes: list[str]
) -> None:
    if not path.endswith(".py"):
        causes.append("A non-Python file changed")
        return
    if after is None:
        causes.append("A file was deleted")
    old = parse_version({path: before.encode()} if before is not None else {}, "L")
    new = parse_version({path: after.encode()} if after is not None else {}, "S")
    if (before is not None and path not in old.entries) or (
        after is not None and path not in new.entries
    ):
        causes.append("An edited Python file is outside the parsed graph")
    if old.graph.parse_errors or new.graph.parse_errors:
        causes.append("An edited version has parse errors")
    if set(old.own_hashes) - new.own_hashes.keys():
        causes.append("A node was removed")
    changed.update(k for k, v in new.own_hashes.items() if old.own_hashes.get(k) != v)
