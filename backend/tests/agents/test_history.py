"""Covered-read pruning with deterministic histories and scripted models only."""

import json
from copy import deepcopy

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import _history, _prompts
from oversight.agents._control import answer_unanswered
from oversight.agents._history import HistoryPruner, estimate_tokens
from oversight.agents.client import Message
from oversight.agents.loop import NO_TOOL_REMINDER
from oversight.agents.tools import TOOL_SCHEMAS, Toolbox
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStep, UsageTick

pytestmark = pytest.mark.anyio
Opened = tuple[Workspace, Tree]


def rows(first: int = 1, last: int = 10, *, whole: bool = False, text: str = "x" * 100) -> str:
    result = "\n".join(f"{n:>5}| {text}" for n in range(first, last + 1))
    note = f"\n[lines {first} to {last} of 100; read more with start_line]"
    return result if whole else result + note


def add(
    history: list[Message], content: str, *, path: str = "x.py", tool: str = "read_file"
) -> Message:
    history.extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "repeated",
                        "type": "function",
                        "function": {
                            "name": tool,
                            # Deliberately unrelated to the returned range.
                            "arguments": json.dumps(
                                {"path": path, "start_line": 900, "end_line": 999}
                            ),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "repeated", "content": content},
        ]
    )
    return history[-1]


def protect(history: list[Message], count: int = 6) -> None:
    for _ in range(count):
        add(history, "files", tool="list_files")


def prune(history: list[Message]) -> None:
    before = estimate_tokens(history, TOOL_SCHEMAS)
    pruner = HistoryPruner(1)
    pruner.prune(history, TOOL_SCHEMAS)
    after = estimate_tokens(history, TOOL_SCHEMAS)
    assert pruner.last_batch_tokens == after
    assert after <= before


@pytest.mark.parametrize(
    "later,covered",
    [
        (rows(20, 30), False),  # Disjoint.
        (rows(1, 20, text="inserted"), False),  # Changed/shifted rows.
        (rows(1, 20), True),
        (rows(50, 60, whole=True, text="changed"), True),
        ("Not run.", False),
        ("Error: no file", False),
        (_prompts.HISTORY_STUB.format(first=1, last=10, path="x.py"), False),
    ],
)
def test_coverage_uses_returned_rows_or_later_whole_file(later: str, covered: bool) -> None:
    history: list[Message] = []
    old = add(history, rows())
    add(history, later)
    protect(history)
    prune(history)
    expected = "[Lines 1-10 of x.py: a later read of this path covers these lines.]"
    assert old["content"] == (expected if covered else rows())


def test_raw_paths_and_all_protected_messages() -> None:
    history: list[Message] = [
        {"role": "system", "content": rows()},
        {"role": "user", "content": "task"},
        {"role": "user", "content": rows()},
    ]
    alias = add(history, rows(), path="./x.py")
    uncovered = add(history, rows(), path="other.py")
    other_tool = add(history, rows(), tool="search")
    old = add(history, rows())
    add(history, rows(1, 20))
    history.append({"role": "user", "content": "delivered message"})
    protect(history, 5)
    recent = add(history, rows())
    snapshot = deepcopy(history)
    prune(history)
    assert str(old["content"]).startswith("[Lines")
    assert all(m["content"] == rows() for m in (alias, uncovered, other_tool, recent))
    for before, after in zip(snapshot, history, strict=True):
        if before is not snapshot[10]:  # Only the eligible tool result can change.
            assert before == after


def test_new_stubs_supply_no_coverage_and_exhaustion_records_estimate() -> None:
    history: list[Message] = [{"role": "user", "content": "keep " * 1000}]
    earlier = add(history, rows(50, 60))
    whole = add(history, rows(1, 10, whole=True))
    add(history, rows(1, 10))
    protect(history)
    prune(history)
    assert str(whole["content"]).startswith("[Lines")
    assert earlier["content"] == rows(50, 60)
    # Eligible results are exhausted even though protected context holds us above low water.
    assert estimate_tokens(history, TOOL_SCHEMAS) > 0.75


@pytest.mark.parametrize("content", ["", "    1| x", "Error: failed", "Not run."])
def test_empty_short_and_non_read_results_are_unchanged(content: str) -> None:
    history: list[Message] = []
    old = add(history, content)
    add(history, rows(whole=True))
    protect(history)
    prune(history)
    assert old["content"] == content


def test_equal_length_and_json_expansion_guards() -> None:
    stub = _prompts.HISTORY_STUB.format(first=1, last=1, path="x.py")
    for path, text in [("x.py", "x" * (len(stub) - 7)), ('"' * 80, "x" * 160)]:
        history: list[Message] = []
        old = add(history, "    1| " + text, path=path)
        add(history, rows(whole=True), path=path)
        protect(history)
        before = deepcopy(history)
        prune(history)
        assert history == before
        assert old["content"] == "    1| " + text


def test_parallel_calls_local_pairing_and_individual_protection() -> None:
    history: list[Message] = []
    old = add(history, rows())
    add(history, "Not run.", tool="search")
    read_assistant = deepcopy(history[0])
    read_assistant["tool_calls"] = [
        {"id": str(i), "function": {"name": "read_file", "arguments": '{"path":"x.py"}'}}
        for i in range(6)
    ]
    history.append(read_assistant)
    recent: list[Message] = [
        {"role": "tool", "tool_call_id": str(i), "content": rows()} for i in range(6)
    ]
    history.extend(recent)
    snapshot = deepcopy(history)
    prune(history)
    assert str(old["content"]).startswith("[Lines")
    assert history[2:] == snapshot[2:]
    answer_unanswered(history)
    assert len(history) == len(snapshot)


def test_threshold_low_water_newest_first_and_rearm(monkeypatch: pytest.MonkeyPatch) -> None:
    history: list[Message] = []
    large = rows(text="x" * 1000)
    oldest = add(history, large)
    newest = add(history, large)
    add(history, large)
    protect(history)
    before = estimate_tokens(history, [])
    pruner = HistoryPruner(int(before) + 1)
    pruner.prune(history, [])
    assert estimate_tokens(history, []) == before  # Below the trigger.
    pruner.threshold = int(before)
    pruner.prune(history, [])
    assert newest["content"] != large and oldest["content"] == large
    assert estimate_tokens(history, []) <= pruner.threshold * 0.75
    assert pruner.last_batch_tokens == estimate_tokens(history, [])

    held: list[Message] = [{"role": "user", "content": "x" * 1000}]
    pruner = HistoryPruner(100)
    calls = []
    original = _history._reads
    monkeypatch.setattr(
        _history, "_reads", lambda messages: (calls.append(1), original(messages))[1]
    )
    pruner.prune(held, [])
    ending = pruner.last_batch_tokens
    pruner.prune(held, [])
    assert calls == [1] and pruner.last_batch_tokens == ending
    held[0]["content"] = "x" * 1099  # 24.75 tokens of growth, just below re-arm.
    pruner.prune(held, [])
    assert calls == [1]
    held[0]["content"] = "x" * 1100  # Exactly 25% of the configured threshold.
    pruner.prune(held, [])
    assert calls == [1, 1]


def test_default_off_is_byte_identical_and_estimate_counts_utf8_and_tools() -> None:
    history: list[Message] = []
    add(history, rows(text='á"\\'))
    add(history, rows(whole=True))
    protect(history)
    baseline = json.dumps(history).encode()
    HistoryPruner(None).prune(history, TOOL_SCHEMAS)
    assert json.dumps(history).encode() == baseline
    encoded = json.dumps({"messages": history, "tools": TOOL_SCHEMAS}, ensure_ascii=False).encode()
    assert estimate_tokens(history, TOOL_SCHEMAS) == len(encoded) / 4
    assert estimate_tokens(history, TOOL_SCHEMAS) > estimate_tokens(history, [])


async def test_real_read_formats_include_cut_row_and_oversized_range(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oversight.agents import tools as module

    workspace, tree = small_tree
    # Force the ordinary range path and the over-file-limit range path on the same file.
    await tree.write_text("long.txt", "x" * 500 + "\n" + "y\n" * 100, expected_hash=None)
    toolbox = Toolbox(workspace, tree, test_timeout_s=60)
    monkeypatch.setattr(module, "MAX_READ_CHARS", 200)
    first = await toolbox.call("read_file", '{"path":"long.txt","end_line":1}')
    monkeypatch.setattr(module, "MAX_READ_CHARS", 40000)
    later = await toolbox.call("read_file", '{"path":"long.txt","end_line":1}')
    assert "line 1 cut" in first.content
    history: list[Message] = []
    old = add(history, first.content)
    add(history, later.content)
    protect(history)
    prune(history)
    assert old["content"] == first.content  # A complete row differs from its cut prefix.
    (tree.root / "big.txt").write_text("z" * 100 + "\n" * 1 + "x\n" * 600_000)
    ranged = await toolbox.call("read_file", '{"path":"big.txt","start_line":1,"end_line":3}')
    assert ranged.ok and "read more with start_line" in ranged.content
    history = []
    old = add(history, ranged.content)
    add(history, ranged.content)
    protect(history)
    prune(history)
    assert old["content"] == "[Lines 1-3 of x.py: a later read of this path covers these lines.]"


@pytest.mark.parametrize("enabled", [False, True])
async def test_pruning_keeps_stale_and_exact_match_edit_checks(
    small_tree: Opened, enabled: bool
) -> None:
    workspace, tree = small_tree
    original = await tree.read_text("shop/mod.py")
    await tree.write_text(
        "shop/mod.py", original.content + "# " + "x" * 1000, expected_hash=original.hash
    )
    toolbox = Toolbox(workspace, tree, test_timeout_s=60)
    content = (await toolbox.call("read_file", '{"path":"shop/mod.py"}')).content
    history: list[Message] = []
    old = add(history, content, path="shop/mod.py")
    add(history, content, path="shop/mod.py")
    protect(history)
    HistoryPruner(1 if enabled else None).prune(history, [])
    assert (old["content"] != content) is enabled
    for old_text, reason in [("absent", "does not appear"), ("value()", "appears more than once")]:
        outcome = await toolbox.call(
            "edit_file",
            json.dumps({"path": "shop/mod.py", "old_text": old_text, "new_text": "changed"}),
        )
        assert not outcome.ok and reason in outcome.content
    current = await tree.read_text("shop/mod.py")
    await tree.write_text("shop/mod.py", current.content + "# user", expected_hash=current.hash)
    stale = await toolbox.call(
        "edit_file",
        json.dumps({"path": "shop/mod.py", "old_text": "return 1", "new_text": "return 2"}),
    )
    assert not stale.ok and "changed since you last read it" in stale.content


async def test_measurement_and_revision_keep_outcomes(small_tree: Opened) -> None:
    workspace, tree = small_tree
    await tree.write_text("notes.txt", ("x" * 100 + "\n") * 100, expected_hash=None)
    turns = [[], [("read_file", {"path": "shop/mod.py"})]]  # Produce the real reminder.
    for i in range(12):
        turns.extend(
            [
                [("read_file", {"path": "notes.txt", "end_line": 50 + i})],
                [
                    (
                        "edit_file",
                        {
                            "path": "shop/mod.py",
                            "old_text": f"return {i + 1}",
                            "new_text": f"return {i + 2}",
                        },
                    )
                ],
            ]
        )
    turns.extend([[("finish", {"summary": "done"})], [("finish", {"summary": "revised"})]])
    estimates = []
    outcomes = []
    for enabled in (False, True):
        current = await tree.read_text("shop/mod.py")
        await tree.write_text(
            "shop/mod.py",
            current.content.replace("return 13", "return 1"),
            expected_hash=current.hash,
        )
        model = script(*turns)
        recorder = Recorder()
        run = make_run(
            (workspace, tree),
            model,
            recorder,
            max_steps=40,
            history_prune_tokens=8000 if enabled else None,
        )
        if enabled:
            run._history.threshold = 2000  # Test trigger only; Settings validation stays intact.
        end = await run.run()
        before = deepcopy(run._messages)
        batch = run._history.last_batch_tokens
        history_state = run._history
        revision = await run.revise("Keep this context", None)
        assert run._history is history_state
        assert run._history.last_batch_tokens == batch
        assert model.requests[-1][:-1] == before
        assert any(m == {"role": "user", "content": NO_TOOL_REMINDER} for m in model.requests[-1])
        errors = [
            step
            for step in recorder.of("AgentStep")
            if isinstance(step, AgentStep) and step.result == "error"
        ]
        assert not errors
        assert end.state == revision.state == "ready_for_review"
        estimates.append(sum(estimate_tokens(m, TOOL_SCHEMAS) for m in model.requests))
        usage = recorder.of("UsageTick")
        assert usage and all(
            isinstance(tick, UsageTick) and tick.input_tokens is None and tick.cost_usd is None
            for tick in usage
        )
        outcomes.append((end, revision, usage))
    assert outcomes[0] == outcomes[1]
    assert estimates[1] < estimates[0]
    print(
        f"history measurement: disabled={estimates[0]:.2f}, enabled={estimates[1]:.2f} "
        "estimated prompt tokens; both ready_for_review, no halts or edit errors"
    )
