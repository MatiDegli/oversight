"""Validated local inputs and the proposal inventory."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from oversight.events import fold_supervision, read_jsonl
from oversight_contracts.events import ChangeProposedEvent, ServerEvent

from ..runner import load_tasks  # noqa: TID252 - Keep runner imports relative, as in the CLI.

type Result = dict[str, object]
type RevisionKey = tuple[str, int]


def change_number(change_id: str) -> int:
    return int(change_id.split("-")[1])


@dataclass
class Inputs:
    events: list[ServerEvent]
    proposals: dict[RevisionKey, ChangeProposedEvent] = field(default_factory=dict)
    bases: dict[str, Result] = field(default_factory=dict)
    results: dict[RevisionKey, Result] = field(default_factory=dict)
    tasks: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.supervision = fold_supervision(self.events)
        self.kinds = {task.id: task.kind for task in load_tasks()}
        self.proposals = {
            (e.payload.change_id, e.payload.revision): e
            for e in self.events
            if e.type == "change.proposed"
        }


def load_inputs(log: Path, results: Path) -> Inputs:
    inputs = Inputs(read_jsonl(log))
    for number, line in enumerate(results.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("kind") not in ("base", "revision"):
                raise ValueError("invalid kind")
            task = row.get("task")
            if not isinstance(task, str) or task not in inputs.kinds:
                raise ValueError("unknown task")
            result = row.get("result")
            if not isinstance(result, dict) or type(result.get("exit_code")) is not int:
                raise ValueError("result requires an integer exit_code")
            if row["kind"] == "base":
                if task in inputs.bases:
                    raise ValueError("repeated base task")
                if inputs.kinds[task] == "tests":
                    raise ValueError("base requires a bug or feature task")
                inputs.bases[task] = result
            else:
                add_revision(inputs, row, task, result)
        except ValueError as error:
            raise ValueError(f"results line {number}: {error}") from error
    return inputs


def add_revision(inputs: Inputs, row: dict[str, object], task: str, result: Result) -> None:
    cid, revision = row.get("change_id"), row.get("revision")
    if not isinstance(cid, str) or not re.fullmatch(r"change-[1-9]\d*", cid):
        raise ValueError("invalid change_id")
    if type(revision) is not int or revision < 1:
        raise ValueError("revision must be an integer >= 1")
    key = (cid, revision)
    if key in inputs.results:
        raise ValueError("repeated change revision")
    if cid in inputs.tasks and inputs.tasks[cid] != task:
        raise ValueError("change names different tasks")
    proposal = inputs.proposals.get(key)
    if proposal is None:
        raise ValueError("no matching change.proposed")
    if row.get("head_commit") != proposal.payload.head_commit:
        raise ValueError("head_commit differs from proposal")
    inputs.tasks[cid] = task
    inputs.results[key] = result


def hidden_status(result: Result | None) -> str:
    return "unknown" if result is None else "pass" if result["exit_code"] == 0 else "fail"
