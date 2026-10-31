"""The standalone worker revalidates typed range data before project execution."""

import pytest

from oversight.workspace.behavior_worker import _arguments

from .test_behavior_worker import invoke


def encoded(data):
    return repr(("oversight.object_arguments.v1", (), {"src": data, "fill": ("literal", None)}))


@pytest.mark.anyio
async def test_worker_rebuilds_real_range_and_keeps_keyword(tmp_path):
    result = await invoke(
        tmp_path,
        "def f(src, *, fill):\n"
        "    assert type(src) is range\n"
        "    assert fill is None\n"
        "    return (src.start, src.stop, src.step, tuple(src))\n",
        [encoded(("range", 0, 5, 1))],
    )
    [row] = result["results"]
    assert row["kind"] == "value"
    assert row["value"][1][:3] == [["int", "0x0"], ["int", "0x5"], ["int", "0x1"]]
    args, kwargs, _ = _arguments(encoded(("range", 0, 5, 1)), tmp_path)
    assert args == () and type(kwargs["src"]) is range


@pytest.mark.anyio
@pytest.mark.parametrize(
    "data,reason",
    [
        (("range", 0, 10001, 1), "range length cap"),
        (("range", 0, 10**100, 1), "range length cap"),
        (("range", 0, 5, 0), "range step is zero"),
        (("range", False, 5, 1), "range requires three int values"),
        (("range", 0, 5.0, 1), "range requires three int values"),
        (("range", 5), "range requires three int values"),
    ],
)
async def test_worker_refuses_invalid_range_data(tmp_path, data, reason):
    result = await invoke(
        tmp_path, "def f(src, *, fill): raise AssertionError('ran')\n", [encoded(data)]
    )
    assert result["results"] == [{"kind": "unrepresentable", "reason": reason}]


@pytest.mark.anyio
async def test_worker_can_refuse_binding_that_static_signature_accepted(tmp_path):
    from oversight.changes import behavior_inputs

    source = "def f(src, *, fill): return tuple(src)\n"
    files = {
        "sample.py": source,
        "test_sample.py": "from sample import f\nf(range(5), fill=None)\n",
    }
    [sample] = behavior_inputs("sample", "f", files, files).inputs
    result = await invoke(
        tmp_path,
        source + "def replacement(): return 0\nf.__code__ = replacement.__code__\n",
        [sample.before],
    )
    assert result["results"][0]["kind"] == "unrepresentable"
