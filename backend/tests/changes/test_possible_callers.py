"""Exact cones cannot authorize code with callers they do not follow; fake models only."""

from dataclasses import replace

import pytest
from test_assessment import full_evidence, latest, make_clock, rec, settled, wait
from test_mode import available, select
from test_policy_profiles import policy_inputs
from test_rules_transaction import configure_transaction

from oversight_contracts.events import ChangeProposed, ChangeResolved, SuiteExecution

pytestmark = pytest.mark.anyio

LIBRARY = """class Utilities:
    def helper(self):
        return 1

    def install(self, replacement):
        self.helper = replacement

    def through_rebound_name(self):
        return self.helper()


def through_unknown_receiver(utilities):
    return utilities.helper()
"""


async def test_boltons_like_empty_exact_cone_is_unknown(h, monkeypatch):
    make_clock(h, monkeypatch)
    available(h)
    await select(h, "merge_when_safe", 0)
    (h.workspace.main.root / "utilities.py").write_text(LIBRARY)
    await h.workspace.main.commit_all("Add public utility helper", h.workspace.user_author())
    await h.manager.rescan()
    await h.run_agent(1, ("utilities.py", "return 1", "return 2"))
    await settled(h)
    [proposal] = h.payloads(ChangeProposed)
    assert proposal.impact.seeds == ["utilities:Utilities.helper"]
    assert proposal.impact.entries == []
    callers = {
        (edge.src, edge.confidence, edge.reason)
        for edge in rec(h).head.graph.edges
        if edge.kind == "calls" and edge.dst == "utilities:Utilities.helper"
    }
    assert callers == {
        ("utilities:Utilities.through_rebound_name", "possible", "rebound_name"),
        ("utilities:through_unknown_receiver", "possible", "unknown_receiver"),
    }
    small_cone = next(rule for rule in latest(h).rules if rule.rule == "small_cone")
    assert small_cone.observed == {
        "nodes": 0,
        "omitted_by_depth": 0,
        "omitted_by_limit": 0,
        "parse_errors": 0,
        "unresolved_removed": 0,
    }
    assert small_cone.evidence
    assert small_cone.result == "unknown"


@pytest.mark.parametrize("with_callers", [True, False])
async def test_rules_waits_for_possible_callers_and_approves_without_them(
    h, monkeypatch, with_callers
):
    clock = make_clock(h, monkeypatch)
    available(h)
    await select(h, "merge_when_safe", 0)
    library = LIBRARY if with_callers else LIBRARY.split("    def install")[0]
    (h.workspace.main.root / "utilities.py").write_text(library)
    await h.workspace.main.commit_all("Add utility library", h.workspace.user_author())
    await h.manager.rescan()
    before = await h.main_head()

    def keep_measured_cone(inputs):
        measured = h.manager._ctx.assessment.inputs(wait(h))
        return replace(
            inputs,
            cone=measured.cone,
            parse_errors=measured.parse_errors,
            unresolved_removed=measured.unresolved_removed,
            possible_callers=measured.possible_callers,
        )

    full_evidence(h, policy_inputs(), keep_measured_cone)
    await h.open(1)
    assert await h.edit(1, "utilities.py", "return 1", "return 2")
    assert await h.manager.proposed("agent-1", "Update helper")
    monkeypatch.setattr(h.workspace._isolation, "_born", True)
    monkeypatch.setattr(h.workspace._isolation, "_checked", True)
    configure_transaction(h, monkeypatch)
    decisions = []
    decide = h.manager._decide

    def track(record, work):
        decisions.append(decide(record, work))
        return decisions[-1]

    monkeypatch.setattr(h.manager, "_decide", track)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert h.manager._ctx.assessment.inputs(wait(h)).possible_callers == (2 if with_callers else 0)
    pending = latest(h)
    assert all(
        r.result == "pass" for r in pending.rules if r.rule not in {"small_cone", "combined_tests"}
    )
    assert next(r for r in pending.rules if r.rule == "small_cone").result == (
        "unknown" if with_callers else "pass"
    )
    if with_callers:
        assert not decisions and not h.payloads(ChangeResolved)
        assert rec(h).state == "ready" and not rec(h).rules_consumed
        assert not any(e.phase == "combined" for e in h.payloads(SuiteExecution))
        clock.advance(121)
        await wait(h).timer
        await settled(h)
        assert not decisions and not h.payloads(ChangeResolved)
        assert rec(h).state == "ready" and await h.main_head() == before
    else:
        assert len(decisions) == 1
        await decisions[0]
        [resolved] = h.payloads(ChangeResolved)
        assert resolved.decision == "approved" and resolved.decided_by == "rules"
        assert rec(h).state == "closed" and await h.main_head() != before
