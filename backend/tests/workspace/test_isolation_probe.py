"""Probe orchestration and JSON output without user namespaces or model calls."""

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from oversight import __main__ as cli
from oversight.settings import REPO_ROOT
from oversight.workspace import Workspace
from oversight.workspace import isolation_probe as probe
from oversight.workspace.isolation import IsolatedRunner, IsolationUnavailable
from oversight.workspace.runner import DEFAULT_OUTPUT_CAP, ProcessResult


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["version", "preflight"])
async def test_probe_unavailable_reports_reason_without_running_suite(
    tmp_path: Path, template: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    runtime = tmp_path / ".runtime"

    async def version(self: IsolatedRunner, cwd: Path) -> str:
        return "unshare from util-linux test"

    async def unavailable(*args: object) -> None:
        raise IsolationUnavailable(f"{stage} blocked")

    async def never(*args: object, **kwargs: object) -> None:
        pytest.fail("an unavailable probe must not prepare a venv or run tests")

    monkeypatch.setattr(IsolatedRunner, "version", version)
    monkeypatch.setattr(IsolatedRunner, stage, unavailable)
    monkeypatch.setattr(Workspace, "ensure_demo_venv", never)
    monkeypatch.setattr(Workspace, "run_tests", never)
    result = await probe.isolation_probe(runtime, template, pytest_pin="test-pin")
    assert result["status"] == "unavailable"
    assert result["reason"] == f"{stage} blocked"
    assert isinstance(result["kernel"], str)
    if stage == "preflight":
        assert result["outside_accepted"] is True
        assert result["util_linux"] == "unshare from util-linux test"
        workspace = Workspace(runtime, template, pytest_pin="test-pin")
        await workspace.acquire_runtime()  # The probe released its lock on failure.
        workspace.release_runtime()
    assert not (runtime / "workspace").exists()
    assert json.loads(json.dumps(result)) == result


@pytest.fixture
def confirmed_runner(
    template: Path, monkeypatch: pytest.MonkeyPatch, code: int, valid_report: bool
) -> list[str]:
    calls: list[str] = []

    async def version(self: IsolatedRunner, cwd: Path) -> str:
        assert cwd == template
        return "unshare from util-linux test"

    async def preflight(self: IsolatedRunner) -> None:
        assert self.port > 0
        calls.append("preflight")

    async def venv(self: Workspace) -> None:
        assert self._pytest_pin == "test-pin"
        assert (self.main.root / "shop/mod.py").exists()
        calls.append("venv")

    async def run(
        self: IsolatedRunner,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = DEFAULT_OUTPUT_CAP,
    ) -> ProcessResult:
        assert calls == ["preflight", "venv"]
        assert timeout_s == 60 and cwd.name == "workspace"
        assert env is not None
        assert argv[0] == str(self.runtime / "demo-venv/bin/python")
        self.entry = {"pid": 1, "refused": True}
        calls.append("suite")
        await asyncio.to_thread(_write_report, Path(env["OVERSIGHT_REPORT"]), code, valid_report)
        return ProcessResult(code, "", "", False, False, 1)

    monkeypatch.setattr(IsolatedRunner, "version", version)
    monkeypatch.setattr(IsolatedRunner, "preflight", preflight)
    monkeypatch.setattr(IsolatedRunner, "__call__", run)
    monkeypatch.setattr(Workspace, "ensure_demo_venv", venv)
    return calls


@pytest.mark.parametrize("code, valid_report", [(0, True), (1, True), (0, False)])
def test_probe_prints_confirmed_or_failed_json_with_real_suite_plumbing(
    tmp_path: Path,
    template: Path,
    capsys: pytest.CaptureFixture[str],
    code: int,
    valid_report: bool,
    confirmed_runner: list[str],
) -> None:
    runtime = tmp_path / ".runtime"
    probe.print_isolation_probe(runtime, template, pytest_pin="test-pin")
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert output.err == ""
    assert set(result) == {
        "kernel",
        "util_linux",
        "status",
        "outside_accepted",
        "entry_confirmed",
        "inside_refused",
        "entry",
        "suite",
        "problems",
    }
    assert result["status"] == ("confirmed" if code == 0 and valid_report else "failed")
    assert result["outside_accepted"] is True
    assert result["entry_confirmed"] is True and result["inside_refused"] is True
    assert result["entry"] == {"pid": 1, "refused": True}
    assert result["suite"]["outcome"] == ("failed" if code else "passed")
    assert result["suite"]["failed"] == code
    assert result["suite"]["passed"] == 1 - code
    assert bool(result["problems"]) is not valid_report
    assert confirmed_runner == ["preflight", "venv", "suite"]
    workspace = Workspace(runtime, template, pytest_pin="test-pin")
    asyncio.run(workspace.acquire_runtime())
    workspace.release_runtime()


def test_probe_cli_passes_the_server_pytest_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / ".runtime"
    calls: list[tuple[Path, Path, str]] = []

    def print_probe(runtime: Path, template: Path, *, pytest_pin: str) -> None:
        calls.append((runtime, template, pytest_pin))

    monkeypatch.setattr(sys, "argv", ["oversight", "isolation-probe", "--runtime", str(runtime)])
    monkeypatch.setattr(cli, "pytest_pin", lambda: "changed-requirement-pin", raising=False)
    monkeypatch.setattr(cli, "print_isolation_probe", print_probe)
    cli.main()
    assert calls == [(runtime, REPO_ROOT / "demo-repo", "changed-requirement-pin")]


def _write_report(report: Path, code: int, valid_report: bool) -> None:
    outcome = "failed" if code else "passed"
    report.write_text(
        json.dumps(
            {
                "kind": "test",
                "nodeid": "tests/test_mod.py::test_value",
                "when": "call",
                "outcome": outcome,
            }
        )
        + "\n"
    )
    failure = "<failure/>" if code else ""
    xml = (
        '<testsuite><testcase classname="tests.test_mod" name="test_value">'
        f"{failure}</testcase></testsuite>"
    )
    report.with_suffix(".xml").write_text(xml if valid_report else "invalid XML")
