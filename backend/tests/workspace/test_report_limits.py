"""Suite machine reports have independent, byte-based streaming ceilings."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from oversight import limits
from oversight.workspace.core import PLUGIN_DIR
from oversight.workspace.measured_report import CallTarget
from oversight.workspace.runner import DEFAULT_OUTPUT_CAP, ProcessResult, run_process
from oversight.workspace.suite import _take_report, parse_report, run_suite


def record(index: int, outcome: str = "passed") -> bytes:
    return (
        json.dumps(
            {
                "kind": "test",
                "nodeid": f"tests/test_long.py::test_parameter[{'x' * 256}-{index:05}]",
                "when": "call",
                "outcome": outcome,
            }
        )
        + "\n"
    ).encode()


def test_report_above_agent_file_ceiling_keeps_all_counts(tmp_path: Path) -> None:
    path = tmp_path / "report.jsonl"
    rows = [record(i) for i in range(3000)]
    rows += [record(3000, "failed"), record(3001, "skipped")]
    data = b"".join(rows)
    assert len(data) > limits.MAX_FILE_BYTES
    path.write_bytes(data)
    lines = _take_report(path)
    assert lines is not None
    tally = parse_report(lines)
    assert (tally.passed, tally.failed, tally.skipped) == (3000, 1, 1)
    assert tally.failed_tests == [json.loads(rows[-2])["nodeid"]]
    assert not path.exists()
    assert _take_report(path) is None


def test_report_reads_only_bounded_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "report.jsonl"
    path.write_bytes(record(0) + record(1))
    handle = path.open("rb")
    guarded = MagicMock(wraps=handle)
    guarded.__enter__.return_value = guarded
    guarded.__exit__.side_effect = handle.__exit__
    guarded.read.side_effect = AssertionError("whole-file read")
    guarded.readlines.side_effect = AssertionError("whole-file readlines")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: guarded)
    lines = _take_report(path)
    assert lines is not None and not isinstance(lines, list)
    assert parse_report(lines).passed == 2
    assert guarded.readline.call_count == 3
    for call in guarded.readline.call_args_list:
        assert 0 < call.args[0] <= limits.MAX_REPORT_LINE_BYTES + 1
    assert handle.closed
    assert not path.exists()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["total", "line"])
@pytest.mark.parametrize("measured", [False, True])
async def test_report_excess_is_explicit_without_partial_counts(
    tmp_path: Path, kind: str, measured: bool
) -> None:
    report: Path | None = None
    (tmp_path / "tmp").mkdir()
    cap = limits.MAX_REPORT_BYTES if kind == "total" else limits.MAX_REPORT_LINE_BYTES

    def write_report(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            first = record(0)
            handle.write(first)
            if kind == "line":
                handle.write(b"x" * (cap + 1))
            else:
                # Valid individual records; only the accumulated bytes exceed the ceiling.
                for _ in range(cap // len(first)):
                    handle.write(first)
        if measured:
            path.with_suffix(".calls").write_text(
                '{"kind":"start","version":1}\n{"kind":"end","problems":[]}\n'
            )
            path.with_suffix(".xml").write_text(
                '<testsuite><testcase file="tests/test_long.py" classname="tests.test_long" '
                'name="test_parameter"/></testsuite>'
            )

    async def spawn(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = DEFAULT_OUTPUT_CAP,
    ) -> ProcessResult:
        nonlocal report
        assert env is not None
        report = Path(env["OVERSIGHT_REPORT"])
        await asyncio.to_thread(write_report, report)
        return ProcessResult(0, "pytest finished", "", False, False, 12)

    run = await run_suite(
        python=tmp_path / "python",
        plugin_dir=tmp_path,
        runtime_root=tmp_path,
        tree_root=tmp_path,
        timeout_s=60,
        redact=lambda text: text,
        spawn=spawn,
        measured_targets=() if measured else None,
    )
    assert report is not None
    reason = f"{report.name} exceeds the {cap}-byte size limit"
    assert run.result.outcome == "error"
    assert (run.result.passed, run.result.failed, run.result.skipped) == (0, 0, 0)
    assert run.result.failed_tests == [reason]
    assert run.output.endswith(reason)
    assert not await asyncio.to_thread(report.exists)
    if measured:
        assert not run.problems
        assert run.measured is not None and not run.measured.complete
        assert "suite incomplete" in run.measured.problems
        assert not list((tmp_path / "tmp").glob("*.calls*"))


def test_exact_byte_boundaries_and_cut_final_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oversight.workspace import suite

    row = record(0).replace(b"x", "é".encode(), 1)
    data = row + row + b'{"kind":'
    monkeypatch.setattr(suite, "MAX_REPORT_BYTES", len(data))
    monkeypatch.setattr(suite, "MAX_REPORT_LINE_BYTES", len(row))
    path = tmp_path / "report.jsonl"
    path.write_bytes(data)
    lines = _take_report(path)
    assert lines is not None
    assert parse_report(lines).passed == 2
    assert not path.exists()


@pytest.mark.anyio
async def test_real_large_parametrized_suite(tmp_path: Path, demo_python: Path) -> None:
    (tmp_path / "test_large.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', range(3000), "
        "ids=lambda n: 'long-parameter-' + 'x' * 256 + f'-{n:05}')\n"
        "def test_parameter(value):\n"
        "    assert value >= 0\n"
    )
    (tmp_path / "tmp").mkdir()
    measured: list[int] = []

    async def spawn(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = DEFAULT_OUTPUT_CAP,
    ) -> ProcessResult:
        result = await run_process(
            argv, cwd=cwd, timeout_s=timeout_s, env=env, output_cap=output_cap
        )
        assert env is not None

        def measure() -> list[int]:
            with Path(env["OVERSIGHT_REPORT"]).open("rb") as handle:
                sizes = [len(row) for row in handle]
            return [sum(sizes), max(sizes), len(sizes)]

        measured.extend(await asyncio.to_thread(measure))
        return result

    run = await run_suite(
        python=demo_python,
        plugin_dir=PLUGIN_DIR,
        runtime_root=tmp_path,
        tree_root=tmp_path,
        timeout_s=60,
        redact=lambda text: text,
        spawn=spawn,
    )
    print(f"plugin measurement: total={measured[0]}, max_line={measured[1]}, rows={measured[2]}")
    assert measured[0] > limits.MAX_FILE_BYTES
    assert measured[2] == 1 + 3 * 3000
    assert run.result.outcome == "passed", run.output
    assert (run.result.passed, run.result.failed, run.result.skipped) == (3000, 0, 0)
    assert not list((tmp_path / "tmp").glob("*.jsonl"))


@pytest.mark.anyio
async def test_call_report_keeps_its_independent_reader_and_bounds(
    tmp_path: Path, demo_python: Path
) -> None:
    (tmp_path / "tmp").mkdir()
    (tmp_path / "app.py").write_text("def caller(text):\n    return text\n")
    (tmp_path / "test_app.py").write_text(
        "from app import caller\ndef test_unicode():\n    assert caller('é' * 512) == 'é' * 512\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    sizes: list[int] = []

    async def spawn(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = DEFAULT_OUTPUT_CAP,
    ) -> ProcessResult:
        result = await run_process(
            argv, cwd=cwd, timeout_s=timeout_s, env=env, output_cap=output_cap
        )
        assert env is not None

        def measure() -> int:
            with Path(env["OVERSIGHT_CALL_REPORT"]).open("rb") as handle:
                return max(len(row) for row in handle)

        sizes.append(await asyncio.to_thread(measure))
        return result

    run = await run_suite(
        python=demo_python,
        plugin_dir=PLUGIN_DIR,
        runtime_root=tmp_path,
        tree_root=tmp_path,
        timeout_s=10,
        redact=lambda text: text,
        spawn=spawn,
        measured_targets=(CallTarget("app:caller", "app.py", "caller", 1, 2),),
    )
    assert sizes[0] > limits.MAX_REPORT_LINE_BYTES
    assert run.result.outcome == "passed", run.output
    assert run.measured is not None and run.measured.complete
    assert len(run.measured.calls) == 1
    assert not list((tmp_path / "tmp").iterdir())
