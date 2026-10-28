"""`ChangeSink`: how the agent runtime reports edits and the end of a run, without importing
`changes` (docs/proposal.md, section 3). The change manager implements it and `app` wires it."""

from typing import Protocol

from oversight.workspace import SuiteRun
from oversight_contracts.common import AgentId
from oversight_contracts.review import FinishDeclaration


class ChangeSink(Protocol):
    async def file_edited(self, agent_id: AgentId, path: str) -> bool:
        """An edit by the agent is on disk in its worktree. The change manager sends
        `change.updated` before this returns, so it precedes the edit's focus and step.

        `True` when the edit was recorded. `False` when it was not: the change manager has sent
        an `error`, and the edit's nodes never reached the UI, so the runtime sends its focus
        with no nodes."""
        ...

    async def tests_executed(self, agent_id: AgentId, run: SuiteRun) -> None:
        """Publish the captured agent suite without adding metadata to its tool result."""
        ...

    async def proposed(
        self,
        agent_id: AgentId,
        summary: str | None,
        *,
        declaration: FinishDeclaration | None = None,
    ) -> bool:
        """The run ended: `finish` was called with `summary`, or the run halted (`None`). The
        change manager sends `change.proposed` before this returns.

        `True` when the change was proposed. `False` when it was not: the change manager has sent
        an `error`, and the runtime ends the run halted, never ready for review."""
        ...
