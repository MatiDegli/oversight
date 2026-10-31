"""The lead's closed, read-main and propose-only tool table."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oversight.agents.tools import MAX_READ_CHARS, MAX_READ_LINES, _function
from oversight.workspace import SECRET_FILE_REFUSAL, FileTooLarge, InvalidPath, is_secret_path
from oversight_contracts.commands import FileContent, TreeResponse
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import PlanZone, SubtaskDraft, validate_dependencies
from oversight_contracts.queue import QueueSnapshot

if TYPE_CHECKING:
    from oversight.agents._lead import LeadLimits

# The schema builder is shared inside agents, without widening its public facade.
# pyright: reportPrivateUsage=false


class LeadProposalError(Exception):
    """A server-owned proposal refusal, adapted at the runtime boundary."""


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Read(_Empty):
    path: str
    start_line: Annotated[int, Field(ge=1)] = 1
    end_line: Annotated[int, Field(ge=1)] | None = None


class _Subtask(_Empty):
    task: str
    depends_on: list[Annotated[int, Field(ge=0, le=5)]] = Field(default_factory=list[int])


class _Plan(_Empty):
    task: str
    subtasks: list[_Subtask]


LEAD_TOOL_SCHEMAS = [
    _function("graph", "Read main's modules and top-level names.", {}, []),
    _function("list_files", "List main's files.", {}, []),
    _function(
        "read_file",
        "Read main with numbered lines.",
        {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        ["path"],
    ),
    _function("queue", "Read work already queued.", {}, []),
    _function(
        "request_plan",
        "Propose a plan for a person to decide.",
        {
            "task": {"type": "string"},
            "subtasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "task": {"type": "string"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0, "maximum": 5},
                        },
                    },
                    "required": ["task"],
                },
            },
        },
        ["task", "subtasks"],
    ),
    _function("finish", "Finish after requesting plans; never wait for agents.", {}, []),
]


@dataclass(frozen=True)
class LeadOutcome:
    content: str
    ok: bool = True
    plan_id: str | None = None
    finish: bool = False


class LeadTools:
    def __init__(
        self,
        *,
        graph: Callable[[], GraphSnapshot],
        tree: Callable[[], Awaitable[TreeResponse]],
        read: Callable[[str], Awaitable[FileContent]],
        queue: Callable[[], QueueSnapshot],
        zone: Callable[[], PlanZone],
        propose: Callable[[str, list[SubtaskDraft]], Awaitable[str]],
        slots: int,
        limits: LeadLimits | None = None,
    ) -> None:
        from oversight.agents._lead import LEAD_LIMITS

        self.graph, self.tree, self.read, self.queue = graph, tree, read, queue
        self.zone, self.propose, self.slots = zone, propose, slots
        self.limits = limits or LEAD_LIMITS
        self.plan_ids: list[str] = []

    async def call(self, name: str, arguments: str) -> LeadOutcome:
        models: dict[str, type[_Empty]] = {
            "graph": _Empty,
            "list_files": _Empty,
            "read_file": _Read,
            "queue": _Empty,
            "request_plan": _Plan,
            "finish": _Empty,
        }
        if name not in models:
            return LeadOutcome(
                f"Unknown tool {name}. The lead's tools are graph, list_files, read_file, "
                "queue, request_plan and finish.",
                False,
            )
        try:
            parsed = models[name].model_validate_json(arguments)
        except ValidationError as error:
            # Do not echo user/model input in validation diagnostics.
            reason = error.errors(include_input=False, include_url=False)[0]["msg"]
            return LeadOutcome(f"Invalid arguments for {name}: {reason}", False)
        if isinstance(parsed, _Plan):
            return await self._request(parsed)
        if isinstance(parsed, _Read):
            return await self._read_file(parsed)
        if name == "graph":
            # Local import avoids the prompt-set/schema import cycle.
            from oversight.agents._plan_call import graph_context

            snapshot = self.graph()
            return LeadOutcome(
                f"Main graph version: {snapshot.version}\n" + graph_context(snapshot)
            )
        if name == "list_files":
            try:
                tree = await self.tree()
            except TimeoutError:
                return LeadOutcome("Listing files timed out.", False)
            paths = "\n".join(file.path for file in tree.files)
            return LeadOutcome(
                paths + ("\n(Listing truncated at 2,000 entries.)" if tree.truncated else "")
            )
        if name == "queue":
            content = self.queue().model_dump_json(exclude_none=True)
            if len(content) > 20_000:
                content = content[:20_000] + "\n(Queue truncated at 20,000 characters.)"
            return LeadOutcome(content)
        return LeadOutcome("Finished.", finish=True)

    async def _read_file(self, request: _Read) -> LeadOutcome:
        path = request.path
        if is_secret_path(path):
            return LeadOutcome(SECRET_FILE_REFUSAL + path.rsplit("/", 1)[-1][:60], False)
        try:
            file = await self.read(path)
        except FileNotFoundError:
            return LeadOutcome(f"There is no file {path[:80]!r} on main.", False)
        except InvalidPath:
            return LeadOutcome("Use a path relative to the repository root.", False)
        except FileTooLarge:
            return LeadOutcome("The file is too large to read.", False)
        lines = file.content.splitlines()
        total, first = len(lines), request.start_line
        end = request.end_line if request.end_line is not None else total
        if (total == 0 and (first != 1 or request.end_line is not None)) or (
            total and not 1 <= first <= end <= total
        ):
            return LeadOutcome(
                f"start_line and end_line must be within 1-{total}, in order.", False
            )
        last = min(end, first + MAX_READ_LINES - 1)
        shown: list[str] = []
        size = 0
        for number in range(first, last + 1):
            row = f"{number:>5}| {lines[number - 1]}"
            if size + len(row) + 1 > MAX_READ_CHARS:
                if not shown:
                    shown.append(row[: MAX_READ_CHARS - 1])
                    last = number
                else:
                    last = number - 1
                break
            shown.append(row)
            size += len(row) + 1
        content = f"{path}: lines {first}-{last} of {total}\n" + "\n".join(shown)
        if last < total:
            content += f"\nRead more with start_line={last + 1}."
        return LeadOutcome(content)

    async def _request(self, request: _Plan) -> LeadOutcome:
        error = self._check(request)
        if error is not None:
            return LeadOutcome(error, False)
        zone = self.zone()
        drafts = [
            SubtaskDraft(task=t.task, zone=zone, depends_on=t.depends_on) for t in request.subtasks
        ]
        try:
            plan_id = await self.propose(request.task, drafts)
        except LeadProposalError as error:
            return LeadOutcome(str(error), False)
        self.plan_ids.append(plan_id)
        return LeadOutcome(
            f"Proposed {plan_id} with {len(drafts)} subtasks. A person decides whether it runs.",
            plan_id=plan_id,
        )

    def _check(self, request: _Plan) -> str | None:
        if len(self.plan_ids) >= self.limits.plans:
            return "This lead already requested 3 plans; call finish."
        if not request.task.strip() or len(request.task) > self.limits.task_chars:
            return "The plan task must be nonblank and at most 2,000 characters."
        if not 1 <= len(request.subtasks) <= self.limits.subtasks:
            return "A plan has 1 to 6 subtasks."
        for index, task in enumerate(request.subtasks):
            if not task.task.strip() or len(task.task) > self.limits.task_chars:
                return f"Subtask {index}: the task must be nonblank and at most 2,000 characters."
        # Dependencies use the contracts' validator; no zone callback runs before admission.
        drafts = [
            SubtaskDraft(task=t.task, zone=PlanZone(node_ids=[], paths=[]), depends_on=t.depends_on)
            for t in request.subtasks
        ]
        try:
            validate_dependencies(drafts)
        except ValueError as error:
            return f"Subtask dependencies: {error}."
        if sum(not task.depends_on for task in request.subtasks) > self.slots:
            return (
                f"At most {self.slots} subtasks may have no depends_on: "
                f"this run offers {self.slots} agent slots."
            )
        return None
