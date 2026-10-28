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
