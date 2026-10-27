"""Level 2 API phases for each clean demo run; no external model service is used."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import smoke
from demo_check_io import (
    DECIDE_DEADLINE,
    PROPOSE_DEADLINE,
    REQUEST_TIMEOUT,
    START_AGENT_DEADLINE,
    EventStream,
    OverallClock,
    expect,
    request,
)
from demo_check_review import (
    LONG_DOC,
    SHORT_DOC,
    read_file,
    refuse_save,
    revision_steps,
    save_review,
    user_node,
)
from oversight_contracts.commands import AgentCreated, FileContent, GraphResponse

SCRIPTS = {
    "A": "fixtures/fake-model/demo-l2-coupon.json",
    "B": "fixtures/fake-model/demo-l2-restock.json",
}
BUDGET_USD = 150.0


def start_agent(base: str, clock: OverallClock | None = None, slot: str = "A") -> AgentCreated:
    """POST /agents with the fake script's task. The server answers only after the demo venv is
    built, which the first run waits for, so the request has START_AGENT_DEADLINE."""
    task = json.loads((smoke.REPO_ROOT / SCRIPTS[slot]).read_text(encoding="utf-8"))["task"]
    reply = expect(
        request(
            base,
            "/agents",
            post={"task": task, "slot": slot, "model": "fake"},
            timeout=START_AGENT_DEADLINE,
            clock=clock,
        ),
        "POST /agents",
        201,
    )
    return AgentCreated.model_validate(json.loads(reply.body))


def graph_of(base: str, clock: OverallClock | None = None) -> GraphResponse:
    timeout = REQUEST_TIMEOUT if clock is None else clock.bounded(REQUEST_TIMEOUT)
    status, body = smoke.fetch_json(f"{base}/graph", time.monotonic() + timeout)
    if clock is not None:
        clock.check()
    if status != 200:
        raise smoke.SmokeError(f"GET /graph answered {status}, not 200")
    try:
        return GraphResponse.model_validate(body)
    except ValidationError as error:
        raise smoke.SmokeError(f"/graph does not validate: {error.errors()[0]['msg']}") from error


def pending_nodes(events: list[Any], agent_id: str) -> set[str]:
    """Project pending overlays as the UI does; GET /graph contains main only."""
    nodes: set[str] = set()
    for event in events:
        p = event.payload
        if getattr(p, "agent_id", None) != agent_id:
            continue
        if event.type in ("change.updated", "change.proposed"):
            nodes = {n.id for n in p.graph.nodes_added} | set(p.graph.nodes_modified)
            nodes.update(p.graph.nodes_removed)
        elif event.type == "change.resolved" and (p.decision == "rejected" or p.merge == "merged"):
            nodes.clear()
    return nodes


def check_overlap(a_step: Any, b_step: Any, a_proposal: Any, b_proposal: Any) -> None:
    if not (a_step.seq < b_proposal.seq and b_step.seq < a_proposal.seq):
        raise smoke.SmokeError(
            "A/B did not overlap: each first step must precede the other's proposal"
        )


def check_usage(event: Any, slot: str) -> None:
    p = event.payload
    if (p.brake, p.budget_usd, p.cost_usd, p.spent_usd) != ("ok", BUDGET_USD, None, 0):
        raise smoke.SmokeError(
            f"{slot} usage.tick: expected brake ok, budget {BUDGET_USD:g}, "
            "unknown cost and zero spend; "
            f"got {p.brake}, {p.budget_usd}, {p.cost_usd}, {p.spent_usd}"
        )


def wait_for(
    stream: EventStream,
    who: AgentCreated,
    kind: str,
    after: int,
    *,
    revision: int | None = None,
    state: str | None = None,
    deadline: float = DECIDE_DEADLINE,
) -> Any:
    # Steps and usage have no change_id in the contract; the created agent owns that change.
    return stream.wait(
        lambda e: e.type == kind and (state is None or e.payload.state == state),
        deadline,
        f"{who.slot} {kind}"
        + (f" {state}" if state else "")
        + (f" revision {revision}" if revision is not None else ""),
        after_seq=after,
        agent_id=who.agent_id,
        change_id=None if kind in ("agent.step", "usage.tick") else who.change_id,
        revision=revision,
    )


def request_revision(
    base: str,
    stream: EventStream,
    clock: OverallClock,
    who: AgentCreated,
    proposal: Any,
) -> Any:
    clock.set_phase(f"{who.slot}: request changes and wait for revising")
    before = graph_of(base, clock).seq
    comment = "Shorten the docstring."
    expect(
        request(
            base,
            f"/changes/{who.change_id}/request-changes",
            post={"comment": comment},
            clock=clock,
        ),
        f"{who.slot} POST request changes",
        202,
    )
    requested = wait_for(stream, who, "change.resolved", before, revision=proposal.payload.revision)
    if (requested.payload.decision, requested.payload.comment) != ("changes_requested", comment):
        raise smoke.SmokeError(
            f"{who.slot} request changes did not preserve its comment and decision"
        )
    return wait_for(stream, who, "agent.status", requested.seq, state="revising")


def stop_working_b(
    base: str,
    stream: EventStream,
    clock: OverallClock,
    b: AgentCreated,
    proposal: Any,
    runtime_dir: Path,
) -> None:
    revising = request_revision(base, stream, clock, b, proposal)
    clock.set_phase("B: stop during work and discard")
    edited = wait_for(stream, b, "change.updated", revising.seq)
    step = stream.wait(
        lambda e: e.type == "agent.step" and e.payload.tool == "edit_file",
        DECIDE_DEADLINE,
        "B revision edit step before Stop",
        after_seq=edited.seq,
        agent_id=b.agent_id,
    )
    if step.payload.result != "ok" or not pending_nodes(stream.seen, b.agent_id):
        raise smoke.SmokeError("B Stop requires a successful working edit with pending nodes")
    tree = runtime_dir / "worktrees" / b.agent_id
    if not tree.is_dir():
        raise smoke.SmokeError("B worktree missing before Stop")
    before = graph_of(base, clock).seq
    expect(request(base, f"/agents/{b.agent_id}/stop", post={}, clock=clock), "B POST stop", 202)
    stopped = wait_for(stream, b, "agent.status", before, state="stopped")
    resolved = wait_for(
        stream, b, "change.resolved", stopped.seq, revision=proposal.payload.revision
    )
    if (resolved.payload.decision, resolved.payload.reason) != ("rejected", "stopped"):
        raise smoke.SmokeError("B Stop did not resolve as rejected/stopped")
    if any(
        e.type == "change.proposed" and e.payload.agent_id == b.agent_id and e.seq > revising.seq
        for e in stream.seen
    ):
        raise smoke.SmokeError(
            "B finished its revision before Stop; cancellation was not exercised"
        )
    # Resolution precedes asynchronous worktree cleanup. Poll only our exact directory.
    end = time.monotonic() + clock.bounded(DECIDE_DEADLINE)
    while tree.exists() and time.monotonic() < end:
        clock.check()
        time.sleep(0.05)
    clock.check()
    if tree.exists():
        raise smoke.SmokeError("B worktree still exists after Stop")
    if pending_nodes(stream.seen, b.agent_id):
        raise smoke.SmokeError("B graph still has pending nodes after Stop")
    expect(
        request(base, f"/files?path=shop/inventory.py&agent={b.agent_id}", clock=clock),
        "B discarded worktree file",
        404,
    )
    main = FileContent.model_validate_json(
        expect(
            request(base, "/files?path=shop/inventory.py", clock=clock), "B main file after Stop"
        ).body
    )
    if main.content != (smoke.REPO_ROOT / "demo-repo/shop/inventory.py").read_text():
        raise smoke.SmokeError("B discarded edit leaked into main")
    graph = graph_of(base, clock).graph
    if any(n.id in {added.id for added in proposal.payload.graph.nodes_added} for n in graph.nodes):
        raise smoke.SmokeError("B discarded added nodes leaked into main's graph")


def approve_a(
    base: str,
    stream: EventStream,
    clock: OverallClock,
    a: AgentCreated,
    proposal: Any,
    initial: GraphResponse,
) -> None:
    clock.set_phase("A: approve revised change and wait for merge delta")
    before = graph_of(base, clock).seq
    expect(
        request(base, f"/changes/{a.change_id}/approve", post={}, clock=clock),
        "A POST approve",
        202,
    )
    resolved = wait_for(stream, a, "change.resolved", before, revision=proposal.payload.revision)
    if (resolved.payload.decision, resolved.payload.merge) != ("approved", "merged"):
        raise smoke.SmokeError(
            f"A revised change was {resolved.payload.merge}, not approved/merged"
        )
    delta = stream.wait(
        lambda e: (
            e.type == "graph.delta"
            and e.payload.cause == "merge"
            and e.payload.commit == resolved.payload.merge_commit
        ),
        DECIDE_DEADLINE,
        "A merge graph.delta",
        after_seq=resolved.seq,
    )
    if delta.payload.from_version != initial.graph.version:
        raise smoke.SmokeError("the merge's graph.delta does not start at the clean graph")
    wait_for(stream, a, "agent.status", delta.seq, state="done")


def edit_main(base: str, stream: EventStream, clock: OverallClock) -> None:
    clock.set_phase("file view: PUT main and wait for new graph node")
    path = "shop/pricing.py"
    original = FileContent.model_validate_json(
        expect(request(base, f"/files?path={path}", clock=clock), "GET main file").body
    )
    before = graph_of(base, clock)
    name = "demo_check_added_function"
    content = original.content + f"\n\ndef {name}() -> int:\n    return 42\n"
    expect(
        request(
            base,
            "/files",
            put={"path": path, "content": content, "base_hash": original.hash},
            clock=clock,
        ),
        "PUT main file",
    )
    delta = stream.wait(
        lambda e: (
            e.type == "graph.delta"
            and e.payload.cause == "user_edit"
            and e.payload.from_version == before.graph.version
        ),
        DECIDE_DEADLINE,
        "file view user_edit graph.delta",
        after_seq=before.seq,
    )
    if not any(
        n.name == name and n.path == path and n.kind == "function"
        for n in delta.payload.nodes_added
    ):
        raise smoke.SmokeError("file view graph.delta is missing the added function")
    saved = FileContent.model_validate_json(
        expect(request(base, f"/files?path={path}", clock=clock), "GET saved main file").body
    )
    if saved.content != content or not any(
        n.name == name for n in graph_of(base, clock).graph.nodes
    ):
        raise smoke.SmokeError("file view save is absent from main's file or graph")


def check_level2(
    base: str,
    initial: GraphResponse,
    stream: EventStream,
    clock: OverallClock,
    runtime_dir: Path,
) -> None:
    clock.set_phase("A/B: parallel POST /agents (venv build)")
    before = graph_of(base, clock).seq
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(start_agent, base, clock, slot) for slot in ("A", "B")]
        try:
            a, b = [job.result(timeout=clock.bounded(START_AGENT_DEADLINE)) for job in jobs]
        except (TimeoutError, smoke.SmokeError):
            clock.check()
            raise
        clock.check()
    if (a.slot, b.slot) != ("A", "B") or a.agent_id == b.agent_id or a.change_id == b.change_id:
        raise smoke.SmokeError("parallel starts did not create distinct A/B agents and changes")
    clock.set_phase("A/B: first steps, proposals and overlap")
    steps = [wait_for(stream, who, "agent.step", before) for who in (a, b)]
    proposals = [
        wait_for(stream, who, "change.proposed", before, revision=1, deadline=PROPOSE_DEADLINE)
        for who in (a, b)
    ]
    check_overlap(*steps, *proposals)
    for who, proposal in zip((a, b), proposals, strict=True):
        if (
            not pending_nodes(stream.seen, who.agent_id)
            or proposal.payload.tests.outcome != "passed"
        ):
            raise smoke.SmokeError(
                f"{who.slot} initial proposal needs pending nodes and passed tests"
            )
        if not proposal.payload.impact.seeds:
            raise smoke.SmokeError(f"{who.slot} initial proposal has no impact seeds")
        wait_for(stream, who, "agent.status", proposal.seq, state="ready_for_review")
        tick = wait_for(stream, who, "usage.tick", before)
        check_usage(tick, who.slot)
    saved, edited = save_review(base, stream, clock, a, proposals[0], graph_of(base, clock).seq)
    if user_node(saved) not in pending_nodes(stream.seen, a.agent_id):
        raise smoke.SmokeError("A pending overlay lost the user edit")
    revising = request_revision(base, stream, clock, a, saved)
    revision_steps(base, stream, clock, a, revising.seq, edited)
    clock.set_phase("A: higher revision of the same change")
    revised = wait_for(
        stream,
        a,
        "change.proposed",
        revising.seq,
        revision=saved.payload.revision + 1,
        deadline=PROPOSE_DEADLINE,
    )
    if revised.payload.tests.outcome != "passed":
        raise smoke.SmokeError("A revised proposal tests did not pass")
    user_node(revised)
    wait_for(stream, a, "agent.status", revised.seq, state="ready_for_review")
    stop_working_b(base, stream, clock, b, proposals[1], runtime_dir)
    approve_a(base, stream, clock, a, revised, initial)
    clock.set_phase("A: refuse review save with no open change")
    before = graph_of(base, clock)
    refuse_save(base, clock, a, read_file(base, clock), closed=True)
    if graph_of(base, clock) != before:
        raise smoke.SmokeError("refused closed review save changed the graph or emitted events")
    if read_file(base, clock).content != edited.content.replace(LONG_DOC, SHORT_DOC):
        raise smoke.SmokeError("A approval lost the user edit or later revision")
    edit_main(base, stream, clock)
    for who in (a, b):
        for event in stream.seen:
            if event.type == "usage.tick" and event.payload.agent_id == who.agent_id:
                check_usage(event, who.slot)
