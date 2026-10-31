"""Iterator consumption is bounded inside the same worker call alarm as execution."""

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
    result = await invoke(tmp_path, source, [consumed()], timeout_s=0.2)
    assert result["problem"] is None
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
@pytest.mark.parametrize("expression", ["[1, 2]", "(1, 2)", "1", "None"])
async def test_worker_refuses_non_iterator_even_when_parent_accepts_call_site(tmp_path, expression):
    from oversight.changes import behavior_inputs

    source = f"def f(): return {expression}\n"
    files = {"sample.py": source, "test_sample.py": "from sample import f\nlist(f())\n"}
    prepared = behavior_inputs("sample", "f", files, files)
    assert prepared.consume_iterator
    [sample] = prepared.inputs
    result = await invoke(tmp_path, source, [consumed(sample.before)])
    assert result["results"] == [
        {"kind": "unrepresentable", "reason": "returned value is not an iterator"}
    ]


@pytest.mark.anyio
async def test_no_observation_marker_keeps_opaque_iterator_behavior(tmp_path):
    result = await invoke(tmp_path, "def f(): return iter((1, 2))\n", ["((), {})"])
    [row] = result["results"]
    assert row["kind"] == "unrepresentable" and "fingerprint" not in row
    assert "observation" not in row
