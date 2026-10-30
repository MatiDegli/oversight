"""Proposal reach uses the agent's graph and names the candidate Git tree."""

from dataclasses import replace

import pytest
from changes_helpers import ADD_THRICE, EDIT, TESTS, TWICE, VALUE, Harness

from oversight.changes._facts import (
    build_change_facts,
    capture_facts,
    validate_change_facts,
)
from oversight_contracts.review import ChangeFacts


@pytest.mark.anyio
async def test_published_reach_names_candidate_and_excludes_containers(h: Harness):
    await h.run_agent(1, EDIT, ADD_THRICE)
    [facts] = h.payloads(ChangeFacts)
    assert ChangeFacts.model_validate_json(facts.model_dump_json()) == facts
    reach = facts.reach
    assert reach is not None and reach.basis == "call_graph" and reach.edges_followed == "exact"
    assert reach.complete and reach.unreached_nodes == ["shop.mod:thrice"]
    assert [e.git_object for e in reach.evidence] == [facts.candidate_tree]
    assert "reach producer unavailable" not in facts.problems


@pytest.mark.anyio
async def test_agent_graph_not_main_decides_reach(h: Harness):
    await h.open(1)
    old = (await h.workspace.worktree("agent-1").read_text(TESTS)).content
    # Main still calls value; the candidate test instead reaches twice.
    new = old.replace("import value", "import twice").replace("assert value()", "assert twice()")
    assert await h.edit(1, TESTS, old, new)
    assert await h.edit(1, "shop/mod.py", "return value() + value()", "return 3")
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert facts.reach is not None and facts.reach.complete
    assert facts.reach.unreached_nodes == []
    rec = h.manager._agents["agent-1"]
    assert TWICE in rec.graph.nodes_modified
    assert not any(e.dst == TWICE for e in rec.base.graph.edges)


@pytest.mark.anyio
async def test_removed_function_stays_unclassified(h: Harness):
    await h.run_agent(
        1, ("shop/mod.py", "\n\n\ndef twice() -> int:\n    return value() + value()\n", "\n")
    )
    [facts] = h.payloads(ChangeFacts)
    assert facts.reach is not None and not facts.reach.complete
    assert facts.reach.unreached_nodes == []


@pytest.mark.anyio
async def test_capture_detaches_reach_and_validator_rejects_forgery(h: Harness):
    from oversight.changes._facts import _reach_facts

    await h.run_agent(1, EDIT)
    rec = h.manager._agents["agent-1"]
    head = rec.head_commit
    assert head is not None
    async with h.workspace.locks(main=True, agent_id="agent-1"):
        evidence = await capture_facts(h.manager._ctx, rec, head=head, revision=rec.revision)
    facts = build_change_facts(evidence)
    assert facts.reach is not None and evidence.reach is not None
    facts.reach.unreached_nodes.append(VALUE)
    assert evidence.reach.unreached_nodes == []
    with pytest.raises(ValueError, match="reach"):
        validate_change_facts(facts, evidence)
    assert build_change_facts(evidence).reach == evidence.reach
    # Bounds use the run's configured cone limits, without claiming coverage.
    rec.task = replace(rec.task, limits=rec.task.limits.model_copy(update={"cone_max_depth": 0}))
    bounded = _reach_facts(rec, evidence.target.candidate_tree)
    assert not bounded.complete and bounded.unreached_nodes == []
