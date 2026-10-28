"""Execute CI's verdict interpreter for every supported status without kernel dependencies."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

from oversight.workspace.runner import run_process

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/ci.yml"


@pytest.mark.anyio
@pytest.mark.parametrize("status, exit_code", [("confirmed", 0), ("unavailable", 0), ("failed", 1)])
async def test_ci_probe_summary_and_verdict(tmp_path: Path, status: str, exit_code: int):
    workflow = WORKFLOW.read_text()
    step = workflow.split("- name: Report network isolation probe", 1)[1].split("\n  browser:")[0]
    assert "uv run --frozen python -m oversight isolation-probe" in step
    script = textwrap.dedent(step.split("python - <<'PY'\n", 1)[1].rsplit("PY", 1)[0])
    verdict = tmp_path / "verdict.json"
    verdict.write_text(json.dumps({"status": status}))
    script = script.replace("/tmp/isolation-probe.json", str(verdict))
    summary = tmp_path / "summary.md"
    result = await run_process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        timeout_s=5,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert result.returncode == exit_code, result.stderr
    assert f"**{status}**" in summary.read_text()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status, exit_code", [("confirmed", 0), ("unavailable", 0), ("failed", 1), ("unknown", 1)]
)
async def test_ci_probe_step_without_python_on_path(tmp_path: Path, status: str, exit_code: int):
    workflow = WORKFLOW.read_text()
    step = workflow.split("- name: Report network isolation probe", 1)[1].split("\n  browser:")[0]
    script = textwrap.dedent(step.split("run: |\n", 1)[1])
    script = script.replace("/tmp/isolation-probe.json", str(tmp_path / "verdict.json"))
    # Only uv is on PATH, as installed by setup-uv; no system Python alias is assumed.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "assert sys.argv[1:4] == ['run', '--frozen', 'python']\n"
        "if sys.argv[4:] == ['-m', 'oversight', 'isolation-probe']:\n"
        "    print(os.environ['PROBE_VERDICT'])\n"
        "else:\n"
        "    os.execv(sys.executable, [sys.executable, *sys.argv[4:]])\n"
    )
    uv.chmod(0o700)
    summary = tmp_path / "summary.md"
    result = await run_process(
        ["/bin/bash", "-eu", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        timeout_s=5,
        env={
            "PATH": str(binaries),
            "PROBE_VERDICT": json.dumps({"status": status}),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
    )
    assert result.returncode == exit_code, result.stderr
    assert f"**{status}**" in summary.read_text()
