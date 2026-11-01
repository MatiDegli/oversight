"""Human HTTP controls and active-drive registration, owned by LiveRun's composition."""

# Like runtime_revisions, these helpers compose LiveRun's private registries.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from oversight.agents import AgentRun, AgentTask, ModelClient, RunControl
from oversight.api import ApiError
from oversight.changes import WrongState
from oversight.workspace import Tree
from oversight_contracts.events import AgentMessageSent, ModelProfile

if TYPE_CHECKING:
    from oversight.app import LiveRun


def prepare(
    live: LiveRun, task: AgentTask, client: ModelClient, tree: Tree, profile: ModelProfile
) -> AgentRun:
    """Register before returning the start response, including a task not yet scheduled."""
    assert live._manager is not None
    run = AgentRun(
        task,
        model=client,
        workspace=live.workspace,
        tree=tree,
        sink=live._manager,
        publish=live._publish,
        ledger=live._usage,
        pricing=live._price_for(profile),
        harness_pack=live._settings.harness_pack,
        history_prune_tokens=live._settings.history_prune_tokens,
    )
    live._kept[task.agent_id] = run
    live._controls[task.agent_id] = run
    return run


def close(live: LiveRun, agent_id: str, *, finished: bool = False) -> None:
    """An older ending drive must leave a newer revision's registration alone."""
    if finished and live._runs.get(agent_id) is not asyncio.current_task():
        return
    current = live._controls.get(agent_id)
    if current is not None:
        live._controls.pop(agent_id)
        current.control.close()


def _eligible(live: LiveRun, agent_id: str) -> RunControl:
    manager = live._manager
    if manager is None:
        raise ApiError("wrong_state", "the run is not open")
    manager.check_control(agent_id)
    run = live._controls.get(agent_id)
    if live._closing or run is None or run.control.closed:
        raise WrongState(f"{agent_id} has no active drive accepting controls")
    return run.control


async def message(live: LiveRun, agent_id: str, text: str) -> None:
    control = _eligible(live, agent_id)

    async def record() -> None:
        masked = live._redactor(text)
        if len(masked) > 4096:
            raise ApiError("invalid_request", "masked message exceeds the 4096-character limit")
        try:
            await live._publish(AgentMessageSent(agent_id=agent_id, text=masked))
        except Exception:
            raise ApiError("internal", "could not record the message") from None

    try:
        accepted = bool(text.strip()) and await control.record_message(text, record)
    except UnicodeEncodeError:
        accepted = False
    if not accepted:
        if control.closed:
            raise WrongState(f"{agent_id} has no active drive accepting controls")
        raise ApiError("invalid_request", "message is blank or exceeds a run message limit")


def pause(live: LiveRun, agent_id: str) -> None:
    _eligible(live, agent_id).pause()


def resume(live: LiveRun, agent_id: str) -> None:
    control = _eligible(live, agent_id)
    if not control.pause_requested:
        raise WrongState(f"{agent_id} has no pause to resume")
    agent = live._agents[agent_id]
    live._check_spending(agent.slot, agent.profile)
    assert live._manager is not None
    if live._manager.main_suspect:
        raise ApiError(
            "wrong_state", "main's state is unknown: reject the change and restart the server"
        )
    control.resume()
