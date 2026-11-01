"""Iterator consumption is bounded inside the same worker call alarm as execution."""

import asyncio
import signal

import pytest

from .test_behavior_worker import invoke


def consumed(literal="((), {})"):
    return repr(("oversight.iterator_items.v1", literal))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source",
    [
        "def f(): return iter((1, True, 'x'))\n",
        "def f(): return zip((1, True, 'x'))\n",
        "from itertools import islice\ndef f(): return islice((1, True, 'x'), 3)\n",
        "def f():\n    yield 1\n    yield True\n    yield 'x'\n",
    ],
)
async def test_finite_iterator_is_consumed_and_type_tagged(tmp_path, source):
    result = await invoke(tmp_path, source, [consumed(), consumed()])
    first, second = result["results"]
    assert first == second
    assert first["kind"] == "value" and first["observation"] == "iterator_items"
    assert first["value"][0] == "list" and first["value"][2:] == [3, "complete"]
    assert first["fingerprint"]
    if "zip" not in source:
        assert [item[0] for item in first["value"][1]] == ["int", "bool", "str"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source",
    [
        "def f(): return iter(range(1000))\n",
        "from itertools import repeat\ndef f(): return repeat(1)\n",
    ],
)
async def test_reaching_item_cap_is_unknown_without_a_partial_list(tmp_path, source):
    result = await invoke(tmp_path, source, [consumed()])
    assert result["results"] == [{"kind": "unrepresentable", "reason": "iterator item cap"}]


@pytest.mark.anyio
async def test_exhaustion_below_cap_is_complete_even_when_display_is_cut(tmp_path):
    result = await invoke(tmp_path, "def f(): return iter(range(999))\n", [consumed()])
    [row] = result["results"]
    assert row["kind"] == "value" and row["fingerprint"]
    assert row["value"][2:] == [999, "cut"]


@pytest.mark.anyio
async def test_consumption_timeout_has_fixed_reason_and_no_fingerprint(tmp_path):
    source = "def f():\n    yield 1\n    while True: pass\n"
    result = await invoke(tmp_path, source, [consumed()], timeout_s=0.3)
    assert result["problem"] is None, f"Expected a successful import before the call: {result}"
    assert result["results"] == [{"kind": "timed_out", "reason": "iterator observation timeout"}]


@pytest.mark.anyio
async def test_mid_consumption_exception_is_the_observed_result(tmp_path):
    source = "def f():\n    yield 1\n    raise ValueError('mid-stream')\n"
    result = await invoke(tmp_path, source, [consumed()])
    [row] = result["results"]
    assert row["kind"] == "raised" and row["class"] == "builtins.ValueError"
    assert row["message"] == "mid-stream" and row["fingerprint"]
    assert row["observation"] == "iterator_items" and "value" not in row


@pytest.mark.anyio
@pytest.mark.parametrize("expression", ["[1, 2]", "(1, 2)", "'text'", "1", "None"])
async def test_non_iterator_keeps_ordinary_observation_with_consumed_call_site(
    tmp_path, expression
):
    from oversight.changes import behavior_inputs

    source = f"def f(): return {expression}\n"
    files = {"sample.py": source, "test_sample.py": "from sample import f\nlist(f())\n"}
    prepared = behavior_inputs("sample", "f", files, files)
    assert prepared.consume_iterator
    [sample] = prepared.inputs
    result = await invoke(tmp_path, source, [sample.before, consumed(sample.before)])
    ordinary, wrapped = result["results"]
    assert ordinary == wrapped
    assert wrapped["kind"] == "value" and wrapped["fingerprint"]
    assert "observation" not in wrapped


@pytest.mark.anyio
async def test_no_observation_marker_keeps_opaque_iterator_behavior(tmp_path):
    result = await invoke(tmp_path, "def f(): return iter((1, 2))\n", ["((), {})"])
    [row] = result["results"]
    assert row["kind"] == "unrepresentable" and "fingerprint" not in row
    assert "observation" not in row


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["cap", "timeout"])
async def test_blocking_generator_finally_does_not_prevent_the_next_call(tmp_path, mode):
    source = (
        "def f(mode):\n"
        "    if mode == 'next': return 42\n"
        "    def values():\n"
        "        try:\n"
        "            if mode == 'cap':\n"
        "                while True: yield 1\n"
        "            yield 1\n"
        "            while True: pass\n"
        "        finally:\n"
        "            while True: pass\n"
        "    return values()\n"
    )
    async with asyncio.timeout(5):
        result = await invoke(
            tmp_path,
            source,
            [consumed(repr(((mode,), {}))), consumed("(('next',), {})")],
            timeout_s=0.3,
        )
    assert result["problem"] is None, f"Expected a successful import before the call: {result}"
    first, second = result["results"]
    assert first == {"kind": "timed_out", "reason": "iterator observation timeout"}
    assert second["kind"] == "value" and second["value"] == ["int", "0x2a"]


@pytest.mark.anyio
async def test_capped_generator_is_closed_and_released_before_the_alarm_is_disabled(tmp_path):
    source = (
        "import signal\n"
        "closed = []\n"
        "def f(mode):\n"
        "    if mode: return closed\n"
        "    def values():\n"
        "        try:\n"
        "            while True: yield 1\n"
        "        finally:\n"
        "            closed.append(signal.getitimer(signal.ITIMER_REAL)[0] > 0)\n"
        "    return values()\n"
    )
    result = await invoke(tmp_path, source, [consumed("((0,), {})"), consumed("((1,), {})")])
    first, second = result["results"]
    assert first == {"kind": "unrepresentable", "reason": "iterator item cap"}
    assert second["value"][1] == [["bool", True]]


def test_timeout_releases_a_generator_paused_at_yield_under_a_fresh_alarm(monkeypatch):
    from oversight.workspace import behavior_worker as worker

    closed = []

    def values():
        try:
            yield 1
        finally:
            closed.append(signal.getitimer(signal.ITIMER_REAL)[0] > 0)

    def interrupted(value):
        next(value)
        raise worker.CallTimeout

    monkeypatch.setattr(worker, "iterator_items", interrupted)
    result = worker.call(values, consumed(), 0.2)
    assert result == {"kind": "timed_out", "reason": "iterator observation timeout"}
    assert closed == [True]


@pytest.mark.anyio
async def test_timeout_cleanup_that_blocks_is_bounded_and_the_target_continues(tmp_path):
    source = (
        "def values():\n"
        "    try: yield 1\n"
        "    finally:\n"
        "        while True: pass\n"
        "class Waiting:\n"
        "    def __init__(self): self.values = values()\n"
        "    def __iter__(self): return self\n"
        "    def __next__(self):\n"
        "        next(self.values)\n"
        "        while True: pass\n"
        "    def close(self): self.values.close()\n"
        "def f(mode): return 42 if mode else Waiting()\n"
    )
    async with asyncio.timeout(5):
        result = await invoke(
            tmp_path, source, [consumed("((0,), {})"), consumed("((1,), {})")], timeout_s=0.3
        )
    assert result["problem"] is None, f"Expected a successful import before the call: {result}"
    first, second = result["results"]
    assert first == {"kind": "timed_out", "reason": "iterator observation timeout"}
    assert second["kind"] == "value" and second["value"] == ["int", "0x2a"]


@pytest.mark.anyio
async def test_cleanup_exception_after_the_cap_cannot_become_a_comparable_result(tmp_path):
    source = (
        "def f():\n"
        "    try:\n"
        "        while True: yield 1\n"
        "    finally: raise ValueError('cleanup')\n"
    )
    result = await invoke(tmp_path, source, [consumed()])
    assert result["results"] == [{"kind": "unrepresentable", "reason": "iterator item cap"}]


@pytest.mark.anyio
@pytest.mark.parametrize("raises", [False, True])
async def test_iterator_close_exception_is_released_inside_the_alarm(tmp_path, raises):
    source = (
        "import signal\n"
        "released = []\n"
        "class Cleanup:\n"
        "    def __del__(self):\n"
        "        released.append(signal.getitimer(signal.ITIMER_REAL)[0] > 0)\n"
        "class Values:\n"
        "    def __iter__(self): return self\n"
        "    def __next__(self):\n"
        f"        if {raises!r}: raise ValueError('iteration')\n"
        "        return 1\n"
        "    def close(self): raise RuntimeError(Cleanup())\n"
        "def f(mode): return released if mode else Values()\n"
    )
    result = await invoke(tmp_path, source, [consumed("((0,), {})"), consumed("((1,), {})")])
    first, second = result["results"]
    if raises:
        assert first["kind"] == "raised" and first["class"] == "builtins.ValueError"
        assert first["message"] == "iteration"
    else:
        assert first == {"kind": "unrepresentable", "reason": "iterator item cap"}
    assert second["value"][1] == [["bool", True]]
