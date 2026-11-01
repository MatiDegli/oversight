"""Real pytest calls are recorded in the demo interpreter, never replayed by the server."""

import json
from pathlib import Path

import pytest

from oversight.workspace.measured_report import CallTarget, read_calls
from oversight.workspace.runner import run_process
from oversight.workspace.suite import run_suite

PLUGIN = Path(__file__).parents[2] / "src/oversight/workspace/report_plugin"


async def record(tmp_path, demo_python, source, tests, *, timeout_s=10):
    (tmp_path / "tmp").mkdir(exist_ok=True)
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "test_app.py").write_text(tests)
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    targets = (CallTarget("app:caller", "app.py", "caller", 1, 100),)
    return await run_suite(
        python=demo_python,
        plugin_dir=PLUGIN,
        runtime_root=tmp_path,
        tree_root=tmp_path,
        timeout_s=timeout_s,
        redact=lambda x: x,
        spawn=run_process,
        measured_targets=targets,
    )


@pytest.mark.anyio
async def test_entry_snapshots_real_fixture_objects_mutation_and_exception(tmp_path, demo_python):
    run = await record(
        tmp_path,
        demo_python,
        """from dataclasses import dataclass
@dataclass
class Item:
    amount: int

def caller(item, values):
    values.append(9)
    if item.amount < 0:
        raise ValueError("negative")
    return item.amount + len(values)
""",
        """import pytest
from app import Item, caller
@pytest.fixture
def item():
    return Item(3)
def test_normal(item):
    assert caller(item, [1]) == 5
def test_exception():
    with pytest.raises(ValueError):
        caller(Item(-1), [])
""",
    )
    assert run.result.outcome == "passed", run.output
    report = run.measured
    assert report is not None and report.complete, report
    assert len(report.calls) == 2
    first, second = report.calls
    assert first.test_id == "test_app.py::test_normal" and first.ordinal == 1
    assert first.inputs != second.inputs
    assert first.inputs is not None and second.result is not None
    assert '"9"' not in first.inputs  # captured before the list was mutated
    assert first.result != second.result
    assert '"raised"' in second.result and "ValueError" in second.result
    assert not list((tmp_path / "tmp").glob("*.calls*"))


@pytest.mark.anyio
async def test_unsupported_object_does_not_call_repr_or_reconstruct(tmp_path, demo_python):
    run = await record(
        tmp_path,
        demo_python,
        """class Item:
    def __repr__(self):
        raise AssertionError("repr must not run")
def caller(item):
    return 1
""",
        """from app import Item, caller
def test_real():
    assert caller(Item()) == 1
""",
    )
    assert run.result.outcome == "passed"
    assert run.measured is not None
    [call] = run.measured.calls
    assert call.problem == "unsupported value type"
    assert call.inputs is None


@pytest.mark.anyio
async def test_per_caller_retention_and_partial_timeout_are_explicit(tmp_path, demo_python):
    run = await record(
        tmp_path,
        demo_python,
        "def caller(x):\n    return x\n",
        """from app import caller
def test_many():
    for i in range(60):
        caller(i)
""",
    )
    assert run.measured is not None
    assert len(run.measured.calls) == 48
    assert not run.measured.complete and "caller invocation cap" in run.measured.problems
    run = await record(
        tmp_path,
        demo_python,
        "def caller(x):\n    return x\n",
        """import threading
from app import caller
def test_cut():
    caller(1)
    threading.Event().wait()
""",
        # The budget includes interpreter startup, plugin loading and collection.
        # Leave room for slow CI; the event blocks indefinitely once the call is recorded.
        timeout_s=8,
    )
    assert run.result.outcome == "timeout"
    assert run.measured is not None
    assert run.measured.calls, run.measured.problems
    assert not run.measured.complete
    assert not list((tmp_path / "tmp").glob("*.calls*"))


@pytest.mark.parametrize("raw", [b"broken", b"{}\n", b"x" * (1_048_576 + 1)])
def test_bounded_reader_rejects_broken_and_oversized_reports(tmp_path, raw):
    report = tmp_path / "calls"
    report.write_bytes(raw)
    result = read_calls(report, ())
    assert not result.complete and result.problems


def test_reader_rejects_duplicate_keys_ordinals_and_missing_end(tmp_path):
    path = tmp_path / "calls"
    records = [
        {"kind": "start", "version": 1},
        {
            "kind": "call",
            "node_id": "app:caller",
            "test_id": "test.py::test_x",
            "ordinal": 1,
            "inputs": ["none"],
            "result": ["value", ["int", "0x1"]],
            "problem": None,
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    report = read_calls(path, (CallTarget("app:caller", "app.py", "caller", 1, 2),))
    assert len(report.calls) == 1 and not report.complete
    path.write_text('{"kind":"start","kind":"end"}\n')
    assert not read_calls(path, ()).complete


@pytest.mark.anyio
async def test_caught_exception_is_a_return_and_recursion_uses_entry_ordinals(
    tmp_path, demo_python
):
    run = await record(
        tmp_path,
        demo_python,
        """def caller(n):
    try:
        if n:
            return caller(n - 1)
        raise ValueError("caught")
    except ValueError:
        return None
""",
        """from app import caller
def test_recursive():
    assert caller(2) is None
""",
    )
    assert run.measured is not None and run.measured.complete
    assert sorted(c.ordinal for c in run.measured.calls) == [1, 2, 3]
    assert all(c.result == '["value",["none"]]' for c in run.measured.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("large", [False, True])
async def test_global_call_and_report_byte_caps(tmp_path, demo_python, large):
    (tmp_path / "tmp").mkdir()
    names = [f"caller_{i}" for i in range(100)]
    value = repr("x" * 512) if large else "None"
    (tmp_path / "app.py").write_text("\n".join(f"def {n}():\n    return {value}\n" for n in names))
    (tmp_path / "test_app.py").write_text(
        "import app\ndef test_many():\n"
        "    for name in dir(app):\n"
        "        if name.startswith('caller_'):\n"
        "            for i in range(48):\n"
        "                getattr(app, name)()\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    targets = tuple(CallTarget(f"app:{name}", "app.py", name, 1, 1000) for name in names)
    run = await run_suite(
        python=demo_python,
        plugin_dir=PLUGIN,
        runtime_root=tmp_path,
        tree_root=tmp_path,
        timeout_s=10,
        redact=lambda s: s,
        spawn=run_process,
        measured_targets=targets,
    )
    assert run.measured is not None and not run.measured.complete
    assert len(run.measured.calls) <= 4096
    assert (
        "recording report byte cap" if large else "global invocation cap"
    ) in run.measured.problems


@pytest.mark.anyio
async def test_snapshot_depth_size_cycles_and_async_are_not_compared(tmp_path, demo_python):
    run = await record(
        tmp_path,
        demo_python,
        "def caller(value):\n    return value\n",
        """from app import caller
def test_limits():
    deep = 0
    for i in range(10):
        deep = [deep]
    cyclic = []
    cyclic.append(cyclic)
    for value in (deep, cyclic, 'x' * 513, list(range(33))):
        caller(value)
""",
    )
    assert run.measured is not None
    assert len(run.measured.calls) == 4 and all(c.problem for c in run.measured.calls)


@pytest.mark.anyio
async def test_cancel_real_pytest_kills_process_and_keeps_partial_calls(tmp_path, demo_python):
    import asyncio
    import os

    from oversight.workspace.suite import SuiteInterrupted

    task = asyncio.create_task(
        record(
            tmp_path,
            demo_python,
            "def caller(x):\n    return x\n",
            """import os, time
from pathlib import Path
from app import caller
def test_wait():
    caller(1)
    (Path(os.environ['HOME']).parent / 'pytest-pid').write_text(str(os.getpid()))
    time.sleep(60)
""",
            timeout_s=30,
        )
    )
    pid_file = tmp_path / "tmp/pytest-pid"
    try:
        async with asyncio.timeout(10):
            for _ in range(1000):
                if pid_file.exists() and (pid_text := pid_file.read_text().strip()):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Pytest did not write non-empty PID text")
        pid = int(pid_text)
        task.cancel()
        with pytest.raises(SuiteInterrupted) as caught:
            await task
        report = caught.value.run.measured
        assert report is not None and report.calls and not report.complete
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert not list((tmp_path / "tmp").glob("*.calls*"))
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.anyio
async def test_aliases_do_not_collapse_into_equal_but_distinct_input_objects(tmp_path, demo_python):
    run = await record(
        tmp_path,
        demo_python,
        "def caller(a, b):\n    return a is b\n",
        """from app import caller
def test_alias():
    value = []
    assert caller(value, value)
def test_distinct():
    assert not caller([], [])
""",
    )
    assert run.measured is not None
    by_test = {c.test_id: c for c in run.measured.calls}
    assert by_test["test_app.py::test_alias"].inputs is None
    assert by_test["test_app.py::test_alias"].problem
    assert by_test["test_app.py::test_distinct"].inputs is not None
