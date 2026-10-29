"""Public behavior samples are revision-bound observations, never private protocol dumps."""

import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from changes_helpers import EDIT, Harness

from oversight.changes._behavior import FunctionBehavior, _compare
from oversight.changes._behavior_inputs import BehaviorInput, BehaviorInputs
from oversight.workspace import BehaviorObservation
from oversight_contracts.review import ChangeFacts


@pytest.fixture
def session_c() -> bool:
    return True


def comparison_fixture(count) -> tuple[Any, dict[str, BehaviorInputs], list[FunctionBehavior]]:
    node = SimpleNamespace(id="sample:f", path="sample.py", name="f")
    rec = SimpleNamespace(head=SimpleNamespace(graph=SimpleNamespace(nodes=[node])))
    inputs = {
        node.id: BehaviorInputs(
            tuple(BehaviorInput(f"f(x={i})", str(i), str(i)) for i in range(count))
        )
    }
    return rec, inputs, [FunctionBehavior(node.id)]


def observation(values):
    return {
        "sample:f": BehaviorObservation(
            tuple(
                {"kind": "value", "value": ["int", hex(i)], "fingerprint": f"{i:064x}"}
                for i in values
            )
        )
    }


@pytest.mark.anyio
async def test_stability_counts_cover_unstable_input_beyond_retained_rows():
    rec, inputs, found = comparison_fixture(10)
    results = [observation(range(10)), observation(range(10, 20))]
    for i in range(10):
        # The last input varies only on repeat, outside the eight retained display rows.
        results.extend([observation([i]), observation([i + 10 if i < 9 else 99])])
    worker = AsyncMock(side_effect=results)
    await _compare(
        cast(Any, SimpleNamespace(run_behavior=worker)),
        rec,
        cast(Any, ["base", "head"]),
        inputs,
        found,
        time.monotonic() + 30,
        set(),
    )
    assert found[0].compared == 10
    assert found[0].differing == 9
    assert len(found[0].rows) == 8
    assert worker.await_count == 22  # Initial execution and one repeat per differing input/side.


@pytest.mark.anyio
async def test_incomplete_repeat_never_certifies_stable_difference():
    rec, inputs, found = comparison_fixture(1)
    worker = AsyncMock(side_effect=[observation([0]), observation([1]), TimeoutError()])
    with pytest.raises(TimeoutError):
        await _compare(
            cast(Any, SimpleNamespace(run_behavior=worker)),
            rec,
            cast(Any, ["base", "head"]),
            inputs,
            found,
            time.monotonic() + 30,
            set(),
        )
    assert found[0].differing == 0
    assert all(row.unstable for row in found[0].rows)


@pytest.mark.anyio
async def test_publication_captures_independent_revision_and_rejects_tampering(
    h: Harness, monkeypatch
):
    from oversight.changes import _proposal
    from oversight.changes._facts import build_change_facts, validate_change_facts

    captured = []
    original = _proposal.publish_facts

    async def publish(ctx, evidence):
        captured.append(evidence)
        await original(ctx, evidence)

    monkeypatch.setattr(_proposal, "publish_facts", publish)
    h.manager._ctx.behavior_diff = True
    await h.run_agent(1, EDIT)
    [facts] = h.payloads(ChangeFacts)
    assert facts.behavior_diff is not None
    assert facts.behavior_diff[0].differing_count == 1
    evidence = captured[0]
    rec = h.manager._agents["agent-1"]
    raw = rec.behavior_diffs[1].functions[0].rows[0].before
    raw["value"] = ["private mutable sentinel"]
    raw["diagnostics"] = "private diagnostic sentinel"
    rec.behavior_diffs.clear()
    assert build_change_facts(evidence) == facts
    tampered = facts.model_copy(deep=True)
    assert tampered.behavior_diff is not None
    original_row = tampered.behavior_diff[0].rows[0]
    tampered.behavior_diff[0].rows[0] = original_row.model_copy(
        update={"after": "private sentinel injection"}
    )
    with pytest.raises(ValueError, match="behavior"):
        validate_change_facts(tampered, evidence)
    with pytest.raises(ValueError, match="behavior"):
        build_change_facts(
            replace(evidence, target=evidence.target.model_copy(update={"revision": 2}))
        )


def test_result_kind_is_part_of_typed_identity():
    from oversight.changes._behavior import _identity

    digest = "f" * 64
    assert _identity({"kind": "value", "fingerprint": digest}) != _identity(
        {"kind": "raised", "fingerprint": digest}
    )


def test_projection_keeps_private_fields_and_diagnostics_out_of_public_samples():
    from oversight.changes._behavior import BehaviorDiff, BehaviorRow
    from oversight.changes._behavior_publish import capture_behavior, project_behavior

    private = "PRIVATE_PROTOCOL_SENTINEL"
    before = {"kind": "value", "value": ["int", "0x1"], "fingerprint": private, "path": private}
    after = {"kind": "raised", "class": "builtins.ValueError", "message": "public", "raw": private}
    measured = BehaviorDiff(
        1,
        (
            FunctionBehavior(
                "sample:f",
                compared=1,
                differing=1,
                rows=(BehaviorRow("f(x=1)", before, after, False),),
                reason=private,
            ),
        ),
    )
    captured = capture_behavior(measured)
    projected = project_behavior(captured, revision=1)
    assert projected is not None
    assert projected[0].not_probed == "probe unavailable"
    assert private not in projected[0].model_dump_json()
    assert '"int","0x1"' in projected[0].rows[0].before
    before["value"] = [private]
    after["message"] = private
    assert project_behavior(captured, revision=1) == projected


@pytest.mark.anyio
async def test_direct_and_caller_publications_capture_and_validate_separate_evidence(
    h, monkeypatch
):
    from oversight.changes import _proposal
    from oversight.changes._behavior_publish import capture_behavior, project_behavior
    from oversight.changes._facts import build_change_facts, validate_change_facts
    from oversight.changes._measured import project_measured

    captured = []
    publish = _proposal.publish_facts

    async def capture(ctx, evidence):
        captured.append(evidence)
        await publish(ctx, evidence)

    monkeypatch.setattr(_proposal, "publish_facts", capture)
    h.manager._ctx.behavior_diff = h.manager._ctx.measured_cone = True
    await h.open(1)
    assert await h.edit(
        1, "tests/test_mod.py", "from shop.mod import value", "from shop.mod import value, twice"
    )
    assert await h.edit(1, "tests/test_mod.py", "assert value() > 0", "assert twice() > 0")
    assert await h.edit(1, *EDIT)
    assert await h.finish(1)
    [facts], [evidence] = h.payloads(ChangeFacts), captured
    rec = h.manager._agents["agent-1"]
    assert facts.behavior_diff == project_behavior(
        capture_behavior(rec.behavior_diffs[1]), revision=1
    )
    assert facts.measured_cone == project_measured(rec.measured_cones[1], evidence.target)
    assert facts.behavior_diff is not None and facts.measured_cone is not None
    direct = {row.node_id: row for row in facts.behavior_diff}
    assert direct["shop.mod:value"].differing_count == 1
    assert "shop.mod:twice" not in direct
    assert [(row.node_id, row.differing_count) for row in facts.measured_cone] == [
        ("shop.mod:twice", 1)
    ]
    rec.behavior_diffs[1].functions[0].rows[0].before["value"] = ["private mutation"]
    rec.behavior_diffs.clear()
    rec.measured_cones.clear()
    assert build_change_facts(evidence) == facts
    assert (
        build_change_facts(replace(evidence, behavior_evidence=None, measured=None)).summary
        == facts.summary
    )
    for field, message in (("behavior_diff", "behavior"), ("measured_cone", "caller")):
        with pytest.raises(ValueError, match=message):
            validate_change_facts(facts.model_copy(update={field: None}), evidence)
