"""Public behavior samples are revision-bound observations, never private protocol dumps."""

import json
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from changes_helpers import EDIT, Harness

from oversight.changes import _proposal
from oversight.changes._behavior import (
    BehaviorDiff,
    BehaviorRow,
    FunctionBehavior,
    _compare,
    _identity,
)
from oversight.changes._behavior_inputs import BehaviorInput, BehaviorInputs, behavior_inputs
from oversight.changes._behavior_publish import MAX_RENDERED, capture_behavior, project_behavior
from oversight.changes._facts import build_change_facts, validate_change_facts
from oversight.changes._measured import project_measured
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
            tuple(BehaviorInput(f"f(x={i})", str(i), str(i), str(i)) for i in range(count))
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


@pytest.mark.parametrize(
    "reason,phrase",
    [
        (None, None),
        ("", "probe unavailable"),
        ("randomness: /private/worker.py", "probe unavailable"),
        ("unrepresentable input; /private/worker.py", "probe unavailable"),
        ("unsupported node kind", "unsupported node kind"),
        ("method or class (not probed yet)", "unsupported node kind"),
        ("added (no base version)", "version unavailable"),
        ("removed (no proposed version)", "version unavailable"),
        ("opaque default requires an explicit argument", "default value not literal"),
        ("required argument has no literal value", "no literal value for a required argument"),
        ("no literal constructor receivers", "no literal receiver"),
        *[
            (reason, "signature not supported")
            for reason in (
                "unknown parameter",
                "argument binding failed",
                "unsupported signature",
                "classmethod",
                "staticmethod",
                "property deleter",
                "other decorator",
                "class bases or metaclass",
                "class decorator",
                "custom object hooks",
                "dunder method",
                "nested class",
                "test module",
                "method target mismatch",
                "unsupported constructor",
                "unsupported dataclass constructor",
                "unsupported getter signature",
                "unsupported setter signature",
            )
        ],
        *[
            (reason, "async or generator")
            for reason in (
                "async or generator",
                "async method",
                "generator method",
            )
        ],
        *[
            (reason, f"side effect: {reason}")
            for reason in (
                "global or nonlocal assignment",
                "environment access",
                "randomness",
                "time access",
                "network access",
                "print or input",
                "file access",
                "property getter effects",
            )
        ],
        *[
            (reason, "input not representable")
            for reason in (
                "unrepresentable input",
                "unrepresentable input or unequal receiver state",
                "no equal typed inputs",
            )
        ],
        ("incomplete repeat", "repeat incomplete"),
        *[
            (reason, "probe failed")
            for reason in (
                "worker failed",
                "import failed on base side",
                "import failed on proposed side",
                "budget exhausted",
                "isolation unavailable",
                "interrupted",
            )
        ],
        ("unknown private diagnostic", "probe unavailable"),
        (
            "incomplete repeat; unrepresentable input; incomplete repeat",
            "input not representable; repeat incomplete",
        ),
        ("unrepresentable input; incomplete repeat", "input not representable; repeat incomplete"),
        ("incomplete repeat; unknown private diagnostic", "probe unavailable"),
    ],
)
def test_not_probed_reason_allowlist(reason, phrase):
    evidence = capture_behavior(BehaviorDiff(1, (FunctionBehavior("sample:f", reason=reason),)))
    [published] = project_behavior(evidence, revision=1) or []
    assert published.not_probed == phrase


@pytest.mark.parametrize(
    "before,after,calls,phrase",
    [
        (
            "DEFAULT = object()\ndef f(value): return value\n",
            "DEFAULT = object()\ndef f(value, mode=DEFAULT): return value\n",
            "f(1)\n",
            "default value not literal",
        ),
        (
            "def f(value): return value\n",
            "def f(value): return value is None\n",
            "f(object())\n",
            "no literal value for a required argument",
        ),
    ],
    ids=["b10-opaque-added-default", "required-untyped-object-call"],
)
def test_unadmitted_function_inputs_publish_specific_reason(before, after, calls, phrase):
    tests = "from sample import f\n" + calls
    generated = behavior_inputs(
        "sample",
        "f",
        {"sample.py": before, "tests/test_sample.py": tests},
        {"sample.py": after, "tests/test_sample.py": tests},
    )
    assert generated.inputs == ()
    evidence = capture_behavior(
        BehaviorDiff(1, (FunctionBehavior("sample:f", reason=generated.reason),))
    )
    [published] = project_behavior(evidence, revision=1) or []
    assert published.not_probed == phrase


def test_method_without_literal_constructor_call_publishes_receiver_reason():
    from test_behavior_methods import PLAIN, prepared

    _, generated, found = prepared(PLAIN, PLAIN.replace("return None", "return 1"), calls="")
    assert not generated
    [published] = (
        project_behavior(capture_behavior(BehaviorDiff(1, tuple(found))), revision=1) or []
    )
    assert published.not_probed == "no literal receiver"


def test_nested_function_publishes_node_kind_reason():
    from oversight.changes._behavior import targets

    module = SimpleNamespace(id="sample", kind="module")
    outer = SimpleNamespace(id="sample:outer", kind="function", parent_id=module.id)
    nested = SimpleNamespace(id="sample:outer.inner", kind="function", parent_id=outer.id)
    version = SimpleNamespace(graph=SimpleNamespace(nodes=[module, outer, nested]))
    rec = SimpleNamespace(
        base=version,
        head=version,
        base_files={},
        head_files={},
        graph=SimpleNamespace(nodes_added=[], nodes_removed=[], nodes_modified=[nested.id]),
    )
    found = targets(cast(Any, rec))
    [published] = project_behavior(capture_behavior(BehaviorDiff(1, found)), revision=1) or []
    assert published.not_probed == "unsupported node kind"


@pytest.mark.anyio
async def test_worker_failure_publishes_fixed_reason_without_path_or_secret():
    rec, inputs, found = comparison_fixture(1)
    path, secret = "/private/worker/project.py", "sk-private-probe-sentinel-123456789"
    worker = AsyncMock(return_value={"sample:f": BehaviorObservation(problem=f"{path}: {secret}")})
    await _compare(
        cast(Any, SimpleNamespace(run_behavior=worker)),
        rec,
        cast(Any, ["base", "head"]),
        inputs,
        found,
        time.monotonic() + 30,
        set(),
    )
    [published] = (
        project_behavior(capture_behavior(BehaviorDiff(1, tuple(found))), revision=1) or []
    )
    assert published.not_probed == "probe failed"
    assert path not in published.model_dump_json()
    assert secret not in published.model_dump_json()


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
    assert found[0].rows == tuple(
        BehaviorRow(
            f"f(x={i})",
            observation([i])["sample:f"].results[0],
            observation([i + 10])["sample:f"].results[0],
            False,
            repeat_complete=True,
            inputs_equal=True,
            input_identity=str(i),
        )
        for i in range(8)
    )
    assert found[0].reason is None
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
    assert found[0].rows == ()


@pytest.mark.anyio
async def test_publication_captures_independent_revision_and_rejects_tampering(
    h: Harness, monkeypatch
):
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
    digest = "f" * 64
    assert _identity({"kind": "value", "fingerprint": digest}) != _identity(
        {"kind": "raised", "fingerprint": digest}
    )


def test_projection_keeps_private_fields_and_diagnostics_out_of_public_samples():
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
@pytest.mark.parametrize("disagreed", [False, True])
async def test_incomplete_repeat_is_counted_but_not_published_as_a_stable_row(disagreed):
    rec, inputs, found = comparison_fixture(1)
    worker = AsyncMock(
        side_effect=[
            observation([0]),
            observation([1]),
            observation([99 if disagreed else 0]),
            {"sample:f": BehaviorObservation(problem="budget exhausted")},
        ]
    )
    await _compare(
        cast(Any, SimpleNamespace(run_behavior=worker)),
        rec,
        cast(Any, ["base", "head"]),
        inputs,
        found,
        time.monotonic() + 30,
        set(),
    )
    projected = project_behavior(capture_behavior(BehaviorDiff(1, tuple(found))), revision=1)
    assert projected is not None
    [published] = projected
    assert published.compared_count == 1
    assert published.differing_count == 0
    assert published.rows == []
    assert published.not_probed == "repeat incomplete"
    assert found[0].reason == "incomplete repeat"


@pytest.mark.anyio
async def test_timeout_after_row_limit_retains_completed_counts_and_samples():
    rec, inputs, found = comparison_fixture(10)
    results = [observation(range(10)), observation(range(10, 20))]
    for i in range(9):
        results.extend([observation([i]), observation([i + 10])])
    worker = AsyncMock(side_effect=[*results, TimeoutError()])
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
    assert found[0].compared == 10
    assert found[0].differing == 9
    assert [row.call for row in found[0].rows] == [f"f(x={i})" for i in range(8)]
    assert not any(row.unstable for row in found[0].rows)


@pytest.mark.parametrize("kind", ["value", "raised"])
@pytest.mark.parametrize("escape", ["\\", '"', "\n", "é", "😀"])
def test_rendered_samples_keep_valid_json_at_escape_boundary(kind, escape):
    # With the old serialized slice, the last character is the first backslash of an escape.
    field = "value" if kind == "value" else "message"
    sample = {"kind": kind, **({"class": "Error"} if kind == "raised" else {})}
    sample[field] = ["str", ""] if kind == "value" else ""
    prefix_length = json.dumps(sample, ensure_ascii=True, separators=(",", ":")).index('""') + 1
    text = "x" * (MAX_RENDERED - prefix_length - 1) + escape * MAX_RENDERED
    sample[field] = ["str", text] if kind == "value" else text
    row = BehaviorRow("f()", sample, sample, False)
    evidence = capture_behavior(BehaviorDiff(1, (FunctionBehavior("sample:f", rows=(row,)),)))
    projected = project_behavior(evidence, revision=1)
    assert projected is not None
    [published] = projected
    for rendered in (published.rows[0].before, published.rows[0].after):
        assert len(rendered) <= MAX_RENDERED
        display = json.loads(rendered)
        assert display["kind"] == kind
        assert "..." in (display[field][1] if kind == "value" else display[field])


def test_large_nested_display_is_capped_before_json_serialization():
    value = ["list", [["str", "é" * 512] for _ in range(32)], 32, "complete"]
    sample = {"kind": "value", "value": value}
    row = BehaviorRow("f()", sample, sample, False)
    evidence = capture_behavior(BehaviorDiff(1, (FunctionBehavior("sample:f", rows=(row,)),)))
    projected = project_behavior(evidence, revision=1)
    assert projected is not None
    rendered = projected[0].rows[0].before
    assert len(rendered) <= MAX_RENDERED
    assert json.loads(rendered)["value"][0] == "list"
    assert "..." in rendered
    assert sample["value"] == value


@pytest.mark.parametrize("field", ["value", "message", "class"])
@pytest.mark.parametrize("raw_dict", [False, True])
def test_untrusted_display_leaves_converge_to_bounded_json(monkeypatch, field, raw_dict):
    leaf = (
        {"x" * MAX_RENDERED + str(i): {"nested": "é" * MAX_RENDERED} for i in range(32)}
        if raw_dict
        else 10 ** (2 * MAX_RENDERED)
    )
    kind = "value" if field == "value" else "raised"
    sample = {"kind": kind, "class": "Error", "message": "message", field: leaf}
    original = json.dumps
    original_sample = original(sample)
    calls = 0

    def bounded_dumps(*args, **kwargs):
        nonlocal calls
        calls += 1
        # Fail promptly on the old infinite loop without hanging the event loop or test worker.
        assert calls <= 32, "display clipping failed to converge"
        return original(*args, **kwargs)

    monkeypatch.setattr("oversight.changes._behavior_publish.json.dumps", bounded_dumps)
    row = BehaviorRow("f()", sample, sample, False)
    evidence = capture_behavior(BehaviorDiff(1, (FunctionBehavior("sample:f", rows=(row,)),)))
    projected = project_behavior(evidence, revision=1)
    assert projected is not None
    for rendered in (projected[0].rows[0].before, projected[0].rows[0].after):
        assert len(rendered) <= MAX_RENDERED
        display = json.loads(rendered)
        assert display["kind"] == kind
        if raw_dict:
            assert isinstance(display[field], dict)
            assert "..." in rendered
        else:
            assert display == {"kind": kind}
    assert original(sample) == original_sample


@pytest.mark.anyio
async def test_incomplete_repeat_appends_to_existing_unrepresentable_reason():
    rec, inputs, found = comparison_fixture(3)
    unrepresentable = {"kind": "unrepresentable"}
    before = BehaviorObservation((unrepresentable, *observation([1, 2])["sample:f"].results))
    after = BehaviorObservation((unrepresentable, *observation([3, 4])["sample:f"].results))
    worker = AsyncMock(
        side_effect=[
            {"sample:f": before},
            {"sample:f": after},
            observation([1]),
            {"sample:f": BehaviorObservation(problem="unavailable")},
            observation([2]),
            {"sample:f": BehaviorObservation(problem="unavailable")},
        ]
    )
    await _compare(
        cast(Any, SimpleNamespace(run_behavior=worker)),
        rec,
        cast(Any, ["base", "head"]),
        inputs,
        found,
        time.monotonic() + 30,
        set(),
    )
    assert found[0].reason == "unrepresentable input; incomplete repeat"
    projected = project_behavior(capture_behavior(BehaviorDiff(1, tuple(found))), revision=1)
    assert projected is not None
    [published] = projected
    assert published.compared_count == 2
    assert published.differing_count == 0
    assert published.rows == []
    assert published.not_probed == "input not representable; repeat incomplete"


@pytest.mark.anyio
async def test_direct_and_caller_publications_capture_and_validate_separate_evidence(
    h, monkeypatch
):
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


def test_public_state_display_is_bounded_and_keeps_worker_metadata_private():
    def result(number):
        return {
            "kind": "value",
            "value": ["none"],
            "fingerprint": str(number),
            "pre_fingerprint": "private input identity",
            "result_fingerprint": "same result",
            "state": {
                "stored": [["text", ["str", "a" * 2000 + str(number), 2001, "cut"]]],
                "properties": [],
            },
        }

    row = BehaviorRow(
        "Box().change()",
        result(0),
        result(1),
        False,
        repeat_complete=True,
        inputs_equal=True,
        input_identity="input",
    )
    evidence = capture_behavior(
        BehaviorDiff(1, (FunctionBehavior("sample:Box.change", 1, 1, (row,)),))
    )
    [function] = project_behavior(evidence, revision=1) or []
    [display] = function.rows
    assert "state" in json.loads(display.before)
    assert len(display.before) <= MAX_RENDERED and len(display.after) <= MAX_RENDERED
    assert all("fingerprint" not in text for text in (display.before, display.after))
    assert evidence and evidence.functions[0].review_samples().samples[0].excluded is not None


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["prestate", "incomplete", "deadline"])
async def test_method_inputs_count_only_after_receiver_prestate_is_reproduced(fault):
    from test_behavior_methods import PLAIN, prepared

    source = PLAIN.replace("amount: int = 0", "").replace("+= amount", "+= 0")
    rec, generated, found = prepared(source)
    assert generated, found[0].reason
    [node_id] = generated

    def result(value, pre="initial"):
        return {
            "kind": "value",
            "value": ["int", hex(value)],
            "fingerprint": str(value),
            "result_fingerprint": str(value),
            "pre_fingerprint": pre,
            "state": {"stored": [], "properties": []},
        }

    def observation(value, pre="initial"):
        return {node_id: BehaviorObservation((result(value, pre),))}

    responses = [
        observation(0),
        observation(1),
        TimeoutError()
        if fault == "deadline"
        else {node_id: BehaviorObservation(problem="unavailable")}
        if fault == "incomplete"
        else observation(0, "different"),
        observation(1),
    ]
    run = AsyncMock(side_effect=responses)
    args = (
        cast(Any, SimpleNamespace(run_behavior=run)),
        cast(Any, rec),
        cast(Any, ["base", "head"]),
        generated,
        found,
        time.monotonic() + 10,
        set(),
    )
    if fault == "deadline":
        with pytest.raises(TimeoutError):
            await _compare(*args)
    else:
        await _compare(*args)
    assert found[0].compared == found[0].differing == 0
    assert not found[0].groups
