"""Shape observations survive proposal publication without deciding its outcome."""

import ast
import asyncio
import threading
from pathlib import Path

import pytest
from changes_helpers import EDIT, PATH, Harness, approve
from pydantic import ValidationError
from test_review_card_lifecycle import enable, settled

from oversight.changes import _shape_facts
from oversight.changes._facts import (
    MISSING_PRODUCERS,
    build_change_facts,
    capture_facts,
    validate_change_facts,
)
from oversight.changes._shape import compare_shape
from oversight.changes._shape_duplicates import compare_duplicates
from oversight.changes._shape_facts import project_shape
from oversight_contracts.review import ChangeFacts, FinishDeclaration

pytestmark = pytest.mark.anyio
TAIL = "    return value() + value()\n"
LONG = "\n\ndef long_function():\n" + "    pass\n" * 80
BODY = (
    "    result = item + 7\n    cleaned = result * 2\n    saved = cleaned + 1\n    return saved\n"
)
DUPLICATES = "\n\ndef a(item):\n" + BODY + "\n\ndef b(item):\n" + BODY
FAILURES = {
    "compare_shape": "shape: size and import checks unavailable",
    "compare_duplicates": "shape: duplicate check unavailable",
}


def published(h: Harness) -> ChangeFacts:
    [facts] = h.payloads(ChangeFacts)
    assert h.types().index("change.proposed") < h.types().index("change.facts_recorded")
    assert facts.complete is False
    return facts


async def add_functions(h: Harness, source: str) -> None:
    await h.run_agent(1, (PATH, TAIL, TAIL + source))


async def recapture(h: Harness):
    rec = h.manager._agents["agent-1"]
    assert rec.head_commit is not None
    async with h.workspace.locks(main=True, agent_id="agent-1"):
        return await capture_facts(h.manager._ctx, rec, head=rec.head_commit, revision=rec.revision)


async def test_clean_edit_checked_empty_with_only_existing_gaps(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, *EDIT)
    assert await h.manager.proposed(
        "agent-1", "Done", declaration=FinishDeclaration(tests_run=True)
    )
    facts = published(h)
    assert facts.shape == [] and facts.shape_complete is True
    assert set(facts.problems) == set(MISSING_PRODUCERS)


async def test_long_function_names_candidate_tree_and_approval_still_merges(h: Harness) -> None:
    await add_functions(h, LONG)
    facts = published(h)
    assert facts.shape is not None and facts.shape_complete is True
    [fact] = facts.shape
    assert (fact.kind, fact.path, fact.node_id) == (
        "function_length",
        PATH,
        "shop.mod:long_function",
    )
    assert fact.message == "`long_function` is new and 81 lines long (limit 80)."
    assert [(ref.git_object, ref.path) for ref in fact.evidence] == [(facts.candidate_tree, PATH)]
    await approve(h)
    assert await h.main_head() != h.base_commit


async def test_import_between_existing_modules(h: Harness) -> None:
    await h.run_agent(1, ("shop/__init__.py", "", "import shop.mod\n"))
    facts = published(h)
    assert facts.shape is not None and facts.shape_complete is True
    [fact] = facts.shape
    assert fact.kind == "import_added"
    assert fact.message == "`shop` now imports `shop.mod`."
    assert [(ref.git_object, ref.path) for ref in fact.evidence] == [
        (facts.candidate_tree, "shop/__init__.py")
    ]


async def test_two_new_identical_functions(h: Harness) -> None:
    await add_functions(h, DUPLICATES)
    facts = published(h)
    assert facts.shape is not None and facts.shape_complete is True
    [fact] = facts.shape
    assert fact.kind == "duplicate_logic"
    assert fact.message == "`a` has the same logic as `b` (shop/mod.py)."
    assert [(ref.git_object, ref.path) for ref in fact.evidence] == [(facts.candidate_tree, PATH)]


async def test_successful_producers_keep_size_then_duplicate_order(h: Harness) -> None:
    await add_functions(h, LONG + DUPLICATES)
    facts = published(h)
    assert facts.shape is not None and facts.shape_complete is True
    assert [fact.kind for fact in facts.shape] == ["function_length", "duplicate_logic"]


async def test_parse_error_reports_both_producer_gaps_after_proposal(h: Harness) -> None:
    await h.run_agent(1, (PATH, "return 1", "return ("))
    facts = published(h)
    assert facts.shape == [] and facts.shape_complete is False
    assert [p for p in facts.problems if p.startswith("shape:")] == [
        "shape: 1 files kept their last good parse",
        "shape: 1 files were not compared for duplicate logic",
    ]


@pytest.mark.parametrize("producer", ["compare_shape", "compare_duplicates", "both"])
@pytest.mark.parametrize("error", [RuntimeError, RecursionError, ValidationError])
async def test_producer_failure_is_sanitized_and_other_facts_publish(
    h: Harness, monkeypatch: pytest.MonkeyPatch, producer: str, error: type[Exception]
) -> None:
    def fail(*args, **kwargs):
        if error is ValidationError:
            raise ValidationError("secret /tmp/x", [])
        raise error("secret /tmp/x")

    names = list(FAILURES) if producer == "both" else [producer]
    for name in names:
        monkeypatch.setattr(_shape_facts, name, fail)
    await add_functions(h, LONG + DUPLICATES)
    facts = published(h)
    assert [p for p in facts.problems if p.startswith("shape:")] == [FAILURES[n] for n in names]
    if producer == "both":
        assert facts.shape is None and facts.shape_complete is None
    else:
        expected = "duplicate_logic" if producer == "compare_shape" else "function_length"
        assert facts.shape is not None and [f.kind for f in facts.shape] == [expected]
        assert facts.shape_complete is False
    for event in h.log:
        assert all(word not in event.envelope for word in ("RuntimeError", "secret", "/tmp/x"))


@pytest.mark.parametrize("producer", ["compare_shape", "compare_duplicates"])
async def test_timeout_bounds_wait_and_still_publishes(
    h: Harness, monkeypatch: pytest.MonkeyPatch, producer: str
) -> None:
    release, exited = threading.Event(), threading.Event()
    real = getattr(_shape_facts, producer)

    def held(*args, **kwargs):
        try:
            release.wait()
            return real(*args, **kwargs)
        finally:
            exited.set()

    monkeypatch.setattr(_shape_facts, "SHAPE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(_shape_facts, producer, held)
    try:
        await h.run_agent(1, EDIT)
        facts = published(h)
        assert facts.shape == [] and facts.shape_complete is False
        assert [p for p in facts.problems if p.startswith("shape:")] == [
            FAILURES[producer].replace("unavailable", "timed out")
        ]
        assert not exited.is_set()  # Timeout did not terminate the worker.
    finally:
        release.set()
        assert await asyncio.to_thread(exited.wait, 2)


async def test_capture_detaches_shape_and_validator_rejects_forgery(h: Harness) -> None:
    await add_functions(h, LONG)
    evidence = await recapture(h)
    facts = build_change_facts(evidence)
    assert facts.shape is not None and evidence.shape is not None
    assert evidence.shape.facts is not None
    facts.shape[0].evidence[0] = (
        facts.shape[0].evidence[0].model_copy(update={"git_object": "invented-tree"})
    )
    facts.shape[0] = facts.shape[0].model_copy(update={"message": "Invented observation"})
    assert evidence.shape.facts[0].message == "`long_function` is new and 81 lines long (limit 80)."
    assert evidence.shape.facts[0].evidence[0].git_object == evidence.target.candidate_tree
    with pytest.raises(ValueError, match="shape"):
        validate_change_facts(facts, evidence)
    fresh = build_change_facts(evidence)
    with pytest.raises(ValueError, match="facts shape differs from captured evidence"):
        validate_change_facts(fresh.model_copy(update={"shape_complete": False}), evidence)
    assert project_shape(None) == (None, None)


async def test_validator_rejects_dropped_capture_problem(h: Harness) -> None:
    await h.run_agent(1, (PATH, "return 1", "return ("))
    evidence = await recapture(h)
    facts = build_change_facts(evidence)
    facts.problems.remove("shape: 1 files were not compared for duplicate logic")
    with pytest.raises(ValueError, match="facts omit shape uncertainty"):
        validate_change_facts(facts, evidence)


async def test_surviving_producer_gaps_precede_failure_text(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("secret /tmp/x")

    monkeypatch.setattr(_shape_facts, "compare_shape", fail)
    await h.run_agent(1, (PATH, "return 1", "return ("))
    facts = published(h)
    assert facts.shape == [] and facts.shape_complete is False
    assert [p for p in facts.problems if p.startswith("shape:")] == [
        "shape: 1 files were not compared for duplicate logic",
        FAILURES["compare_shape"],
    ]


async def test_producers_run_in_order_off_loop_with_detached_source_maps(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_functions(h, LONG)
    rec = h.manager._agents["agent-1"]
    loop_thread, calls = threading.get_ident(), []

    def check(base, head, changed):
        assert threading.get_ident() != loop_thread
        assert base is rec.base.graph and head is rec.head.graph
        assert isinstance(changed, frozenset)
        assert changed == {n.id for n in rec.graph.nodes_added} | set(rec.graph.nodes_modified)

    def sized(base, head, changed, **trees):
        check(base, head, changed)
        calls.append("size")
        return compare_shape(base, head, changed, **trees)

    def duplicated(base, head, base_files, head_files, changed, **trees):
        check(base, head, changed)
        assert base_files == rec.base_files and base_files is not rec.base_files
        assert head_files == rec.head_files and head_files is not rec.head_files
        calls.append("duplicates")
        return compare_duplicates(base, head, base_files, head_files, changed, **trees)

    monkeypatch.setattr(_shape_facts, "compare_shape", sized)
    monkeypatch.setattr(_shape_facts, "compare_duplicates", duplicated)
    evidence = await recapture(h)
    assert evidence.shape is not None and evidence.shape.complete is True
    assert calls == ["size", "duplicates"]


async def test_cards_republication_preserves_captured_shape(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    cards, _ = enable(h, monkeypatch)
    await add_functions(h, LONG + DUPLICATES)
    await settled(cards)
    first, last = h.payloads(ChangeFacts)
    assert first.shape and first.shape == last.shape
    assert first.shape_complete is last.shape_complete is True


async def test_capture_cancellation_propagates(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    await h.run_agent(1, EDIT)
    loop, entered = asyncio.get_running_loop(), asyncio.Event()
    release, exited = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        try:
            release.wait()
        finally:
            exited.set()

    monkeypatch.setattr(_shape_facts, "compare_shape", held)
    task = asyncio.create_task(recapture(h))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(exited.wait, 2)


def test_shape_has_no_decision_readers() -> None:
    root = Path(__file__).parents[2] / "src" / "oversight"
    readers = {
        path.name
        for path in root.rglob("*.py")
        if any(
            isinstance(node, ast.Attribute | ast.Name)
            and isinstance(node.ctx, ast.Load)
            and (node.attr if isinstance(node, ast.Attribute) else node.id)
            in {"shape", "shape_complete"}
            for node in ast.walk(ast.parse(path.read_text()))
        )
    }
    assert readers == {"_facts.py", "_shape_facts.py"}
