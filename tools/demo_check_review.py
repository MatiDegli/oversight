"""Editable review checks through the API and correlated SSE, on the scripted fake only."""

from hashlib import sha256
from typing import Any

import smoke
from demo_check_io import DECIDE_DEADLINE, EventStream, OverallClock, expect, request
from oversight_contracts.commands import AgentCreated, ErrorResponse, FileContent, FileSaved

PATH = "shop/pricing.py"
USER_NODE = "demo_review_user_edit"
USER_EDIT = f"\n\ndef {USER_NODE}() -> int:\n    return 42\n"
LONG_DOC = "Take the coupon's percent off the total; unknown codes change nothing."
SHORT_DOC = "Apply a known coupon; otherwise keep the total."


def read_file(base: str, clock: OverallClock, agent_id: str | None = None) -> FileContent:
    query = f"?path={PATH}" + (f"&agent={agent_id}" if agent_id else "")
    return FileContent.model_validate_json(
        expect(request(base, "/files" + query, clock=clock), "GET review file").body
    )


def refuse_save(
    base: str,
    clock: OverallClock,
    who: AgentCreated,
    file: FileContent,
    *,
    closed: bool = False,
) -> None:
    """Use a valid path/hash so wrong_state, rather than stale_file, guards the save."""
    main = read_file(base, clock)
    error = ErrorResponse.model_validate_json(
        expect(
            request(
                base,
                "/files",
                put={
                    "path": PATH,
                    "content": file.content + "# forbidden review save\n",
                    "base_hash": file.hash,
                    "agent_id": who.agent_id,
                },
                clock=clock,
            ),
            "PUT review without an idle open change",
            409,
        ).body
    ).error
    if (
        error.code != "wrong_state"
        or who.change_id not in error.message
        or "ready" not in error.message
    ):
        raise smoke.SmokeError("review refusal must explain that this change needs ready state")
    if read_file(base, clock) != main:
        raise smoke.SmokeError("refused review save wrote to main")
    if not closed and read_file(base, clock, who.agent_id) != file:
        raise smoke.SmokeError("refused working review save wrote to A's worktree")
    if closed:
        expect(
            request(base, f"/files?path={PATH}&agent={who.agent_id}", clock=clock),
            "closed A still has no review file after refused save",
            404,
        )


def user_node(event: Any) -> str:
    nodes = [
        n.id for n in event.payload.graph.nodes_added if n.name == USER_NODE and n.path == PATH
    ]
    if len(nodes) != 1:
        raise smoke.SmokeError("review pending graph is missing the user's added function")
    return nodes[0]


def save_review(
    base: str,
    stream: EventStream,
    clock: OverallClock,
    who: AgentCreated,
    proposal: Any,
    before: int,
) -> tuple[Any, FileContent]:
    clock.set_phase("A: PUT editable new side while idle")
    main = read_file(base, clock)
    file = read_file(base, clock, who.agent_id)
    content = file.content + USER_EDIT
    saved = FileSaved.model_validate_json(
        expect(
            request(
                base,
                "/files",
                put={
                    "path": PATH,
                    "content": content,
                    "base_hash": file.hash,
                    "agent_id": who.agent_id,
                },
                clock=clock,
            ),
            "A PUT editable new side",
        ).body
    )
    if saved.path != PATH or saved.hash != sha256(content.encode()).hexdigest():
        raise smoke.SmokeError("review save returned the wrong path or content hash")
    updated = stream.wait(
        lambda e: e.type == "change.updated" and e.payload.file.path == PATH,
        DECIDE_DEADLINE,
        "A user review change.updated",
        after_seq=before,
        agent_id=who.agent_id,
        change_id=who.change_id,
    )
    if (updated.payload.author, updated.payload.file.original, updated.payload.file.modified) != (
        "user",
        main.content,
        content,
    ):
        raise smoke.SmokeError("review change.updated lost user attribution or full file contents")
    node = user_node(updated)
    if node not in updated.payload.node_ids:
        raise smoke.SmokeError("review change.updated omits the user's pending node id")
    proposed = stream.wait(
        lambda e: e.type == "change.proposed",
        DECIDE_DEADLINE,
        "A re-proposal after user review save",
        after_seq=updated.seq,
        agent_id=who.agent_id,
        change_id=who.change_id,
        revision=proposal.payload.revision + 1,
    )
    if proposed.payload.tests.outcome != "passed" or user_node(proposed) != node:
        raise smoke.SmokeError(
            "review save must re-propose passed tests and the user's pending node"
        )
    edited = read_file(base, clock, who.agent_id)
    if edited.content != content or edited.hash != saved.hash or read_file(base, clock) != main:
        raise smoke.SmokeError("review save must update only A's worktree with the user's content")
    return proposed, edited


def revision_steps(
    base: str,
    stream: EventStream,
    clock: OverallClock,
    who: AgentCreated,
    after: int,
    edited: FileContent,
) -> None:
    clock.set_phase("A: stale edits refused until re-read, then preserve user edit")
    for _ in range(2):
        step = stream.wait(
            lambda e: e.type == "agent.step" and e.payload.tool == "edit_file",
            DECIDE_DEADLINE,
            "A stale edit_file failure",
            after_seq=after,
            agent_id=who.agent_id,
        )
        if (
            step.payload.result != "error"
            or step.payload.error != f"{PATH} changed since you last read it; read it again"
        ):
            raise smoke.SmokeError("A edit_file must fail on the user's edit until A reads again")
        if any(
            e.payload.agent_id == who.agent_id
            and e.seq > after
            and (
                e.type == "change.updated"
                or (e.type == "agent.step" and e.payload.tool == "read_file")
            )
            for e in stream.seen
            if e.type in ("change.updated", "agent.step")
        ):
            raise smoke.SmokeError("A re-read or wrote before its stale edit was refused")
        after = step.seq
    refuse_save(base, clock, who, edited)
    read = stream.wait(
        lambda e: e.type == "agent.step" and e.payload.tool == "read_file",
        DECIDE_DEADLINE,
        "A re-read after stale edit",
        after_seq=after,
        agent_id=who.agent_id,
    )
    if read.payload.result != "ok":
        raise smoke.SmokeError("A must successfully re-read before editing again")
    updated = stream.wait(
        lambda e: e.type == "change.updated" and e.payload.file.path == PATH,
        DECIDE_DEADLINE,
        "A revision preserving user edit",
        after_seq=read.seq,
        agent_id=who.agent_id,
        change_id=who.change_id,
    )
    expected = edited.content.replace(LONG_DOC, SHORT_DOC)
    if (
        expected == edited.content
        or updated.payload.author != "agent"
        or updated.payload.file.modified != expected
    ):
        raise smoke.SmokeError("A's revision must shorten the docstring and keep the user's edit")
    user_node(updated)
    step = stream.wait(
        lambda e: e.type == "agent.step" and e.payload.tool == "edit_file",
        DECIDE_DEADLINE,
        "A successful edit_file after re-read",
        after_seq=updated.seq,
        agent_id=who.agent_id,
    )
    if step.payload.result != "ok" or read_file(base, clock, who.agent_id).content != expected:
        raise smoke.SmokeError("A's edit after re-read did not persist the combined revision")
