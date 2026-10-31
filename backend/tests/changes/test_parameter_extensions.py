"""New-parameter observations publish separately, without behavior counts or cards."""

import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from backend.tests.workspace.test_behavior_parameter_extensions import invoke_extension
from changes_helpers import Harness

from oversight.changes import _behavior as behavior
from oversight.changes import _proposal
from oversight.changes._behavior import BehaviorDiff, FunctionBehavior
from oversight.changes._behavior_publish import (
    capture_behavior,
    project_behavior,
    project_extensions,
)
from oversight.changes._facts import build_change_facts, validate_change_facts
from oversight.changes._parameter_extensions import ExtensionProbe
from oversight.workspace import BehaviorObservation
from oversight_contracts.review import ChangeFacts


async def measure(tmp_path, before, after, tests, *, problem=None, method=None):
    node = SimpleNamespace(
        id="sample:" + (method or "f"),
        path="sample.py",
        name=method.split(".")[-1] if method else "f",
        kind="method" if method else "function",
    )
    imports = "from sample import " + (method.split(".")[0] if method else "f") + "\n"
    rec = SimpleNamespace(
        base_files={"sample.py": before},
        head_files={"sample.py": after, "test_sample.py": imports + tests},
        head=SimpleNamespace(graph=SimpleNamespace(nodes=[node])),
    )
    probe = ExtensionProbe()
    deadline = time.monotonic() + 30
    probe.prepare(cast(Any, rec), [FunctionBehavior(node.id)], deadline)
    requests = []

    async def run_behavior(tree, targets, **_):
        [target] = targets
        requests.append(target)
        if problem:
            return {node.id: BehaviorObservation(problem=problem)}
        result = await invoke_extension(
            tmp_path / tree, before if tree == "base" else after, target
        )
        return {node.id: BehaviorObservation(tuple(result["results"]), result["problem"])}

    await probe.measure(
        cast(Any, SimpleNamespace(run_behavior=run_behavior)), cast(Any, ["base", "head"]), deadline
    )
    assert requests[0] is requests[1]
    assert len(requests) == 2
    assert requests[0].observe_binding
    measured = BehaviorDiff(1, (), probe.results)
    evidence = capture_behavior(measured)
    public = project_extensions(evidence, revision=1)
    assert public and project_behavior(evidence, revision=1) == []
    assert evidence and all(row.observation is None for row in evidence.extensions[0].rows)
    return public[0], measured, evidence


@pytest.mark.anyio
async def test_single_run_extension_rows_do_not_claim_disagreeing_repeats(tmp_path):
    result, measured, _ = await measure(
        tmp_path, "def f(): return 1\n", "def f(extra=0): return extra\n", "f(extra=3)\n"
    )
    assert not measured.extensions[0].rows[0].unstable
    assert not result.rows[0].unstable


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [False, True])
async def test_proposed_keyword_records_rejection_or_kwargs_value(tmp_path, kwargs):
    before = "def f(**kwargs): return kwargs['extra']\n" if kwargs else "def f(): return 1\n"
    result, _, _ = await measure(
        tmp_path, before, "def f(*, extra=0): return extra + 1\n", "f(extra=3)\n"
    )
    assert result.parameters == ["extra"]
    assert result.run_count == 1 and result.rejected_before == int(not kwargs)
    [row] = result.rows
    assert row.inputs == "f(extra=3)"
    assert row.after.startswith('{"kind":"value"') and "0x4" in row.after
    assert row.before.startswith('{"kind":"value"' if kwargs else '{"kind":"raised"')
    assert result.not_probed is None
    assert "binding_rejected" not in row.before and "fingerprint" not in row.before


@pytest.mark.anyio
async def test_run_counts_include_all_supplied_calls_while_rows_are_capped_at_eight(tmp_path):
    result, _, _ = await measure(
        tmp_path,
        "def f(): return 1\n",
        "def f(extra=0): return extra\n",
        "\n".join(f"f(extra={i})" for i in range(12)),
    )
    assert result.run_count == result.rejected_before == 12
    assert len(result.rows) == 8
    assert result.not_probed is None


@pytest.mark.anyio
async def test_worker_unavailable_is_explicit_not_zero_observations_claimed_as_success(tmp_path):
    result, _, _ = await measure(
        tmp_path,
        "def f(): return 1\n",
        "def f(extra=0): return extra\n",
        "f(extra=3)",
        problem="import failed",
    )
    assert result.run_count == result.rejected_before == 0
    assert result.rows == [] and result.not_probed == "probe failed"


@pytest.mark.anyio
async def test_unrepresentable_results_report_partial_recording(tmp_path):
    result, _, _ = await measure(
        tmp_path,
        "def f(): return 1\n",
        "def f(extra=0): return iter(()) if extra else 1\n",
        "f(extra=0)\nf(extra=3)\n",
    )
    assert result.run_count == result.rejected_before == 1
    assert len(result.rows) == 1 and result.not_probed == "input not representable"


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [False, True])
async def test_method_extension_runs_the_same_receiver_and_invocation_on_both_sides(
    tmp_path, kwargs
):
    before = "class Box:\n    def __init__(self, x): self.x = x\n" + (
        "    def f(self, **kwargs): return self.x\n"
        if kwargs
        else "    def f(self): return self.x\n"
    )
    after = (
        "class Box:\n    def __init__(self, x): self.x = x\n"
        "    def f(self, *, extra=0): return self.x + extra\n"
    )
    result, _, _ = await measure(tmp_path, before, after, "Box(2).f(extra=3)\n", method="Box.f")
    assert result.node_id == "sample:Box.f" and result.parameters == ["extra"]
    assert result.run_count == 1 and result.rejected_before == int(not kwargs)
    assert result.rows[0].inputs == "Box(2).f(extra=3)"
    assert "0x5" in result.rows[0].after


@pytest.mark.anyio
async def test_generator_extension_uses_the_existing_bounded_item_observation(tmp_path):
    result, _, _ = await measure(
        tmp_path, "def f(): yield 1\n", "def f(extra=0): yield extra\n", "list(f(extra=3))\n"
    )
    assert result.run_count == result.rejected_before == 1
    assert result.rows[0].before.startswith('{"kind":"raised"')
    assert result.rows[0].after.startswith("items of iterator: ")


@pytest.mark.anyio
async def test_supplied_input_cap_reports_omitted_calls_separately_from_row_retention(tmp_path):
    result, _, _ = await measure(
        tmp_path,
        "def f(): return 1\n",
        "def f(extra=0): return extra\n",
        "\n".join(f"f(extra={i})" for i in range(49)),
    )
    assert result.run_count == result.rejected_before == 48
    assert len(result.rows) == 8 and result.not_probed == "recording budget reached"


def test_unavailable_preserves_the_prepared_supplied_input_cap_reason():
    rec = SimpleNamespace(
        base_files={"sample.py": "def f(): return 1\n"},
        head_files={
            "sample.py": "def f(extra=0): return extra\n",
            "test_sample.py": "from sample import f\n"
            + "\n".join(f"f(extra={i})" for i in range(49)),
        },
        head=SimpleNamespace(
            graph=SimpleNamespace(
                nodes=[SimpleNamespace(id="sample:f", path="sample.py", name="f", kind="function")]
            )
        ),
    )
    probe = ExtensionProbe()
    probe.prepare(cast(Any, rec), [FunctionBehavior("sample:f")], time.monotonic() + 30)
    assert len(probe.inputs["sample:f"].samples) == 48
    assert probe.inputs["sample:f"].reason == "budget exhausted"

    probe.unavailable("worker failed")

    [result] = probe.results
    assert result.reason == "budget exhausted"
    assert result.run_count == result.rejected_before == 0 and result.rows == ()
    public = project_extensions(capture_behavior(BehaviorDiff(1, (), probe.results)), revision=1)
    assert public and public[0].not_probed == "recording budget reached"


@pytest.mark.anyio
async def test_extension_capture_detaches_worker_results_and_checks_revision(tmp_path):
    result, measured, evidence = await measure(
        tmp_path, "def f(): return 1\n", "def f(extra=0): return extra\n", "f(extra=3)"
    )
    measured.extensions[0].rows[0].after["value"] = ["int", "tampered"]
    assert project_extensions(evidence, revision=1) == [result]
    with pytest.raises(ValueError, match="another revision"):
        project_extensions(evidence, revision=2)


@pytest.mark.anyio
async def test_proposal_facts_keep_extensions_out_of_behavior_counts_and_cards(
    h: Harness, monkeypatch
):
    captured = []
    original = _proposal.publish_facts

    async def publish(ctx, evidence):
        captured.append(evidence)
        await original(ctx, evidence)

    monkeypatch.setattr(_proposal, "publish_facts", publish)
    await h.workspace.check_isolation()
    h.manager._ctx.behavior_diff = True
    await h.run_agent(
        1,
        ("shop/mod.py", "def value()", "def value(*, extra=0)"),
        ("tests/test_mod.py", "assert value() > 0", "assert value(extra=3) > 0"),
    )
    [facts] = h.payloads(ChangeFacts)
    assert facts.parameter_extensions is not None
    [extension] = facts.parameter_extensions
    assert extension.node_id == "shop.mod:value"
    assert extension.run_count == extension.rejected_before == 1
    assert len(extension.rows) == 1 and extension.not_probed is None
    assert facts.behavior_diff is not None
    [ordinary] = [row for row in facts.behavior_diff if row.node_id == "shop.mod:value"]
    assert ordinary.compared_count > 0 and ordinary.differing_count == 0
    assert ordinary.rows == [] and facts.review_cards is None
    [evidence] = captured
    assert build_change_facts(evidence) == facts
    assert evidence.behavior_evidence is not None
    with pytest.raises(ValueError, match="parameter extensions"):
        validate_change_facts(facts.model_copy(update={"parameter_extensions": None}), evidence)
    with pytest.raises(ValueError, match="parameter extensions"):
        validate_change_facts(
            facts,
            replace(evidence, behavior_evidence=replace(evidence.behavior_evidence, extensions=())),
        )


@pytest.mark.anyio
async def test_isolation_failure_retains_extension_not_probed(h: Harness, monkeypatch):
    h.manager._ctx.behavior_diff = True
    worker = AsyncMock(side_effect=AssertionError("must not start without isolation"))
    monkeypatch.setattr(h.workspace, "run_behavior", worker)
    await h.run_agent(
        1,
        ("shop/mod.py", "def value()", "def value(*, extra=0)"),
        ("tests/test_mod.py", "assert value() > 0", "assert value(extra=3) > 0"),
    )
    [facts] = h.payloads(ChangeFacts)
    assert facts.parameter_extensions
    assert facts.parameter_extensions[0].not_probed == "recording budget reached"
    assert facts.parameter_extensions[0].run_count == 0
    worker.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [False, True])
async def test_changed_receiver_state_excludes_rejections_and_values(tmp_path, kwargs):
    before = "class Box:\n    def __init__(self, x): self.x = x\n" + (
        "    def f(self, **kwargs): return self.x\n"
        if kwargs
        else "    def f(self): return self.x\n"
    )
    after = (
        "class Box:\n    def __init__(self, x): self.x = x + 1\n"
        "    def f(self, extra=0): return self.x\n"
    )
    result, measured, _ = await measure(
        tmp_path, before, after, "Box(2).f(extra=3)", method="Box.f"
    )
    assert result.run_count == result.rejected_before == 0 and result.rows == []
    assert measured.extensions[0].reason == "unrepresentable input or unequal receiver state"
    assert result.not_probed == "input not representable"


@pytest.mark.anyio
@pytest.mark.parametrize("aliased", [False, True])
@pytest.mark.parametrize("changed", [False, True])
async def test_constructor_arguments_share_static_guards_including_local_class_aliases(
    tmp_path, aliased, changed
):
    cls = "class Box:\n    def __init__(self, x): self.x = x\n"
    before = cls + "def f(value, **kwargs): return value.x\n"
    after = (cls.replace("self.x = x", "self.x = x + 1") if changed else cls) + (
        "def f(value, extra=0): return value.x\n"
    )
    call = (
        "def test_it():\n    Local = Box\n    f(Local(2), extra=3)\n"
        if aliased
        else "f(Box(2), extra=3)\n"
    )
    result, measured, _ = await measure(tmp_path, before, after, "from sample import Box\n" + call)
    assert result.run_count == int(not changed) and result.rejected_before == 0
    assert len(result.rows) == int(not changed)
    assert measured.extensions[0].reason == (
        "unrepresentable input or unequal receiver state" if changed else None
    )


@pytest.mark.anyio
async def test_unrepresentable_result_preserves_the_supplied_input_cap_reason(tmp_path):
    result, measured, _ = await measure(
        tmp_path,
        "def f(): return 1\n",
        "def f(extra=0): return iter(()) if extra else 1\n",
        "\n".join(f"f(extra={i})" for i in range(49)),
    )
    assert result.run_count == result.rejected_before == 1 and len(result.rows) == 1
    assert measured.extensions[0].reason == "budget exhausted; unrepresentable input"
    assert result.not_probed == "input not representable; recording budget reached"


@pytest.mark.anyio
async def test_counted_prepare_runs_first_without_hiding_kwargs_extensions(monkeypatch):
    node = SimpleNamespace(id="sample:f", path="sample.py", name="f", kind="function")
    rec = SimpleNamespace(
        base_files={"sample.py": "def f(**kwargs): return 1\n"},
        head_files={
            "sample.py": "def f(extra=0): return extra\n",
            "test_sample.py": "from sample import f\nf(extra=3)",
        },
        head=SimpleNamespace(graph=SimpleNamespace(nodes=[node])),
        task=SimpleNamespace(limits=SimpleNamespace(test_timeout_s=30)),
    )
    order = []
    original_counted, original_extension = behavior._prepare, ExtensionProbe.prepare

    def counted(*args):
        order.append("counted")
        return original_counted(*args)

    def extension(self, record, found, deadline):
        order.append("extension")
        assert found[0].reason is None
        original_extension(self, record, found, deadline)

    async def whole(action):
        return await action

    monkeypatch.setattr(behavior, "targets", lambda _: (FunctionBehavior(node.id),))
    monkeypatch.setattr(behavior, "_prepare", counted)
    monkeypatch.setattr(ExtensionProbe, "prepare", extension)
    ctx = SimpleNamespace(
        whole=whole, workspace=SimpleNamespace(isolation=SimpleNamespace(available=False))
    )
    measured = await behavior.probe_behavior(
        cast(Any, ctx), cast(Any, rec), head="head", revision=1
    )
    assert order == ["counted", "extension"]
    assert measured.functions[0].reason == "unsupported signature"
    assert measured.extensions[0].parameters == ("extra",)
