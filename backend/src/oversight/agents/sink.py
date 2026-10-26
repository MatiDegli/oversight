"""`ChangeSink`: how the agent runtime reports edits and the end of a run, without importing
`changes` (docs/proposal.md, section 3). The change manager implements it and `app` wires it."""

from typing import Protocol

from oversight_contracts.common import AgentId


class ChangeSink(Protocol):
    async def file_edited(self, agent_id: AgentId, path: str) -> None:
        """An edit by the agent is on disk in its worktree. The change manager sends
        `change.updated` before this returns, so it precedes the edit's focus and step."""
        ...

    async def proposed(self, agent_id: AgentId, summary: str | None) -> None:
        """The run ended: `finish` was called with `summary`, or the run halted (`None`). The
        change manager sends `change.proposed` before this returns."""
        ...
