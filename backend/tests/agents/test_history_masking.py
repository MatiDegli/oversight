"""Observation masking uses deterministic histories and scripted models, never a provider."""

import json
from copy import deepcopy
from typing import cast

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import _history, _prompts
from oversight.agents._history import HistoryPruner, estimate_tokens
from oversight.agents.client import Message
from oversight.workspace import Tree, Workspace

pytestmark = pytest.mark.anyio


def add(history: list[Message], name: str, content: str, arguments: str = "{}") -> Message:
    history.extend(
        [
            {
                "role": "assistant",
                "content": "Keep this reasoning " * 30,
                "tool_calls": [
                    {"id": "same-id", "function": {"name": name, "arguments": arguments}}
                ],
            },
            {"role": "tool", "tool_call_id": "same-id", "content": content},
        ]
    )
    return history[-1]


def protect(history: list[Message]) -> None:
    for _ in range(10):
        add(history, "list_files", "recent " * 200)


@pytest.mark.parametrize("name", ["read_file", "search", "list_files", "find_symbol"])
def test_mask_stub_names_exact_call_with_compact_arguments(name: str) -> None:
    history: list[Message] = []
    arguments = '{ "path": "shop/cart.py", "start_line": 1 }'
    old = add(history, name, "old " * 300, arguments)
    protect(history)
    before = deepcopy(history)
    HistoryPruner(1).prune(history, [])
    assert old["content"] == (
        f'[Output of {name} {{"path":"shop/cart.py","start_line":1}} '
        "cleared to save context; call it again if you need it.]"
    )
    assert history[0] == before[0] and history[2:] == before[2:]


def test_argument_truncation_is_at_most_200_characters_and_one_line() -> None:
    history: list[Message] = []
    arguments = json.dumps({"query": 'á\n"' + "a" * 300})
    old = add(history, "search", "result " * 300, arguments)
    protect(history)
    HistoryPruner(1).prune(history, [])
    compact = json.dumps(json.loads(arguments), separators=(",", ":"))
    expected = compact[:199] + "…"
    assert len(expected) == 200
    assert old["content"] == _prompts.HISTORY_MASK_STUB.format(name="search", arguments=expected)
    assert "\n" not in str(old["content"])
    assert cast(list[dict], history[0]["tool_calls"])[0]["function"]["arguments"] == arguments


@pytest.mark.parametrize(
    "headline",
    [
        "Ran tests: 3 of 4 pass",
        "Ran tests: the suite could not run",
        "Tests reached the 60 s limit. Results are incomplete.",
    ],
)
def test_tests_keep_the_complete_headline(headline: str) -> None:
    history: list[Message] = []
    old = add(history, "run_tests", headline + "\n" + "details\n" * 200)
    protect(history)
    HistoryPruner(1).prune(history, [])
    assert old["content"] == headline + _prompts.HISTORY_TEST_STUB


def test_only_named_observations_change_and_only_when_shorter() -> None:
    history: list[Message] = [
        {"role": role, "content": "keep " * 1000} for role in ("system", "user")
    ]
    for name in ("edit_file", "finish", "unknown"):
        add(history, name, "unchanged " * 500)
    stub = _prompts.HISTORY_MASK_STUB.format(name="search", arguments="{}")
    for content in ("", "tiny", stub, "x" * len(stub)):
        add(history, "search", content)
    add(history, "read_file", _prompts.HISTORY_STUB.format(first=1, last=10, path="x.py"))
    add(history, "read_file", _prompts.HISTORY_STUB.format(first=1, last=10, path="x\n" * 300))
    add(history, "run_tests", "Headline" + _prompts.HISTORY_TEST_STUB)
    add(history, "read_file", "retain invalid JSON " * 100, "{")
    # Even a long one-line test result cannot be shortened while keeping its headline.
    add(history, "run_tests", "headline " * 100)
    protect(history)
    before = deepcopy(history)
    HistoryPruner(1).prune(history, [])
    assert history == before


@pytest.mark.parametrize("name", ["read_file", "search", "list_files", "find_symbol", "run_tests"])
def test_oldest_first_stops_immediately_at_low_water_and_is_idempotent(name: str) -> None:
    history: list[Message] = []
    oldest = add(history, name, "Headline\n" + "x" * 40000)
    second = add(history, "read_file", "y" * 2000)
    protect(history)
    before = deepcopy(history)
    threshold = int(estimate_tokens(history, []))
    HistoryPruner(threshold).prune(history, [])
    assert oldest["content"] != before[1]["content"]
    assert second == before[3]
    assert estimate_tokens(history, []) <= threshold * _prompts.HISTORY_LOW_WATER
    again = deepcopy(before)
    HistoryPruner(threshold).prune(again, [])
    assert again == history  # Independent instances, same input.
    HistoryPruner(threshold).prune(history, [])
    assert again == history


def test_exhausted_masking_is_idempotent_and_rearms_only_after_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history: list[Message] = [{"role": "user", "content": "x" * 10000}]
    add(history, "search", "old " * 1000)
    add(history, "run_tests", "Headline\n" + "details " * 1000)
    protect(history)
    scans = []
    original = _history._mask

    def counted(*args):
        scans.append(1)
        return original(*args)

    monkeypatch.setattr(_history, "_mask", counted)
    pruner = HistoryPruner(1000)
    pruner.prune(history, [])
    snapshot = deepcopy(history)
    assert pruner.last_batch_tokens == estimate_tokens(history, []) > 750
    pruner.prune(history, [])
    assert scans == [1] and history == snapshot
    HistoryPruner(1000).prune(history, [])  # Idempotence without relying on rearm state.
    assert history == snapshot
    history[0]["content"] = "x" * 10999
    pruner.prune(history, [])
    assert scans == [1, 1]
    history[0]["content"] = "x" * 11000
    pruner.prune(history, [])
    assert scans == [1, 1, 1]


def test_covered_reads_run_before_oldest_masking() -> None:
    history: list[Message] = []
    search = add(history, "search", "s" * 2000)
    content = "    1| " + "x" * 40000
    covered = add(history, "read_file", content, '{"path":"x.py"}')
    add(history, "read_file", content, '{"path":"x.py"}')
    protect(history)
    threshold = int(estimate_tokens(history, []))
    HistoryPruner(threshold).prune(history, [])
    assert covered["content"] == _prompts.HISTORY_STUB.format(first=1, last=1, path="x.py")
    assert search["content"] == "s" * 2000


def test_parallel_calls_protect_exactly_the_last_ten_individual_calls() -> None:
    history: list[Message] = []
    add(history, "search", "old " * 1000)
    assistant = deepcopy(history[0])
    call = cast(list[dict], assistant["tool_calls"])[0]
    assistant["tool_calls"] = [deepcopy(call) for _ in range(10)]
    history.append(assistant)
    history.extend(
        {"role": "tool", "tool_call_id": "same-id", "content": "recent " * 1000} for _ in range(10)
    )
    before = deepcopy(history)
    HistoryPruner(1).prune(history, [])
    assert history[1] != before[1]
    assert history[2:] == before[2:]


async def test_refetch_is_unmasked_and_four_real_results_still_halt_within_30_steps(
    small_tree: tuple[Workspace, Tree],
) -> None:
    workspace, tree = small_tree
    await tree.write_text("notes.txt", "x" * 1000 + "\n", expected_hash=None)
    read = [("read_file", {"path": "notes.txt"})]
    searches = [("search", {"query": f"absent-{i}"}) for i in range(10)]
    model = script(read, searches, read, read, read)
    run = make_run((workspace, tree), model, Recorder(), max_steps=30, history_prune_tokens=8000)
    run._history.threshold = 1  # Exercise the policy without changing startup validation.
    end = await run.run()
    assert end.steps < 30
    assert end.detail == "Stopped: the same call gave the same result 4 times."
    assert "Output of read_file" in str(model.requests[2][3]["content"])
    # The fetched result is new and protected, even though the old result stays masked.
    assert str(model.requests[3][-1]["content"]).startswith("    1| ")
    assert "Output of read_file" in str(model.requests[3][3]["content"])
