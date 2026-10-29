"""Proposal observations cannot authorize limits on a different prepared combination."""

from dataclasses import replace

import pytest
import test_rules_transaction as tx_tests
from changes_helpers import EDIT, assert_main_clean, bounded
from test_assessment import full_evidence, latest, make_clock, rec, terminals
from test_mode import available, select
from test_policy_profiles import policy_inputs

from oversight_contracts.events import ChangeProposed, ChangeResolved
from oversight_contracts.policy import ChangeAssessment

pytestmark = pytest.mark.anyio


@pytest.fixture
def reserved(h, monkeypatch):
    async def reserve(case):
        clock = make_clock(h, monkeypatch)
        available(h)
        await select(h, "merge_when_safe", 0)
        full_evidence(h, policy_inputs())  # Deliberately small, passing proposal observations.
        root = h.workspace.main.root
        tree = await h.open(1)
        if case == "possible":
            # Main gains a rebound name after the agent opened its exact-only version.
            with (root / EDIT[0]).open("a") as stream:
                stream.write("\nvalue = staticmethod(value)\n")
            await h.workspace.main.commit_all("Rebind main helper", h.workspace.user_author())
            await h.manager.rescan()
        if case.startswith("cone"):
            count = int(case[-1]) - 1  # twice is already a caller.
            with (root / EDIT[0]).open("a") as stream:
                for n in range(count):
                    stream.write(f"\ndef caller_{n}():\n    return value()\n")
            await h.workspace.main.commit_all("Add main callers", h.workspace.user_author())
            await h.manager.rescan()
        await h.edit(1, *EDIT)
        if case in {"files3", "files4"}:
            for n in range(int(case[-1]) - 1):
                (tree.root / f"extra_{n}.txt").write_text("extra\n")
        elif case.startswith("lines"):
            # One replaced line (2) plus added comments: exact inclusive 60-line boundary.
            with (tree.root / EDIT[0]).open("a") as stream:
                stream.write("# added\n" * (int(case[5:]) - 2))
        elif case == "removed":
            path = tree.root / EDIT[0]
            path.write_text(path.read_text().split("\n\ndef twice")[0] + "\n")
        elif case == "parse":
            (tree.root / "broken.py").write_text("def broken(:\n")
        elif case in {"depth", "limit"}:
            field = "cone_max_depth" if case == "depth" else "cone_max_nodes"
            rec(h).task = replace(
                rec(h).task, limits=rec(h).task.limits.model_copy(update={field: 0})
            )
        assert await h.manager.proposed("agent-1", "Measure exact combination")
        monkeypatch.setattr(h.workspace._isolation, "_born", True)
        decisions, decide = [], h.manager._decide

        def track(record, work):
            decisions.append(decide(record, work))
            return decisions[-1]

        monkeypatch.setattr(h.manager, "_decide", track)
        # Proposal suites precede the required-only transaction adapter.
        tx_tests.configure_transaction(h, monkeypatch)
        return clock, decisions

    return reserve


@pytest.mark.parametrize(
    "case",
    [
        "files3",
        "files4",
        "lines60",
        "lines61",
        "cone5",
        "cone6",
        "removed",
        "depth",
        "limit",
        "parse",
        "possible",
    ],
)
@bounded
async def test_actual_combination_limits(h, reserved, case):
    reservation = await reserved(case)
    pre = await h.main_head()
    await tx_tests.execute(h, reservation)
    pending, definitive = h.payloads(ChangeAssessment)
    assert latest(h) == pending
    if case.startswith("cone"):
        assert len(h.payloads(ChangeProposed)[0].impact.entries) == 1
        assert definitive.candidate_tree != pending.candidate_tree
    assert all(r.result == "pass" for r in pending.rules if r.rule != "combined_tests")
    passes = case in {"files3", "lines60", "cone5"}
    if passes:
        [resolution] = h.payloads(ChangeResolved)
        assert resolution.assessment == definitive
        assert all(r.result == "pass" for r in definitive.rules)
    else:
        assert not h.payloads(ChangeResolved) and rec(h).state == "ready"
        assert await h.main_head() == pre
        rule = "small_change" if case in {"files4", "lines61"} else "small_cone"
        assert next(r for r in definitive.rules if r.rule == rule).result == (
            "unknown" if case == "possible" else "fail"
        )
    assert not terminals(h)
    await assert_main_clean(h)


async def test_parse_errors_in_captured_main_are_not_hidden_by_proposal(h, reserved, monkeypatch):
    # Immutable object input contains the error; suite and proposal observations are irrelevant.
    from oversight.changes import _candidate

    reservation = await reserved("files3")
    real = _candidate.python_files

    async def invalid(ctx, tree):
        return {**await real(ctx, tree), "broken.py": "def broken(:\n"}

    monkeypatch.setattr(_candidate, "python_files", invalid)
    await tx_tests.execute(h, reservation)
    assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    assert (
        next(r for r in h.payloads(ChangeAssessment)[-1].rules if r.rule == "small_cone").observed[
            "parse_errors"
        ]
        == 2
    )
    assert not terminals(h)


async def test_rules_scan_failure_still_delivers_single_resolution(h, reserved, monkeypatch):
    reservation = await reserved("files3")

    async def failed(*args):
        raise RuntimeError("Scripted scan failure")

    monkeypatch.setattr(h.manager._ctx, "scan", failed)
    await tx_tests.execute(h, reservation)
    assert len(h.payloads(ChangeResolved)) == 1 and rec(h).merged
    assert rec(h).next_snapshot is None and not terminals(h)
