"""Demo environments, redaction and suite results."""

import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from backend.tests.workspace.workspace_helpers import GitOut, MakeWorkspace
from backend.tests.workspace.workspace_helpers import pytestmark as pytestmark

from oversight.workspace import (
    ProcessResult,
    WorkspaceError,
)
from oversight.workspace import core as workspace_core
from oversight_contracts.events import SuiteResult

# --- Demo venv and redaction ------------------------------------------------------------------


def interpreter_exists(path: str) -> bool:
    return Path(path).exists()


class FakeUv:
    """Stands in for the runner: records calls, builds a fake venv, and answers the probe. Like the
    real runner, starting an interpreter that is not there raises `FileNotFoundError`."""

    def __init__(self, root: Path, *, probe_ok: bool = True, install_ok: bool = True) -> None:
        self.root = root
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.probe_ok = probe_ok
        self.install_ok = install_ok

    async def __call__(self, argv: Any, *, env: Any = None, **_: Any) -> ProcessResult:
        argv = tuple(argv)
        self.calls.append((argv, dict(env or {})))
        code = 0
        if argv[:3] == ("uv", "--no-config", "venv"):
            (self.root / "demo-venv" / "bin").mkdir(parents=True)
            (self.root / "demo-venv" / "bin" / "python").write_text("")
        elif "pip" in argv and not self.install_ok:
            code = 1
        elif argv[0].endswith("python"):
            if not interpreter_exists(argv[0]):  # absent, or a symlink whose target is gone
                raise FileNotFoundError(2, "No such file or directory", argv[0])
            code = 0 if self.probe_ok else 1
        return ProcessResult(code, "", "boom" if code else "", False, False, 1)

    @property
    def commands(self) -> list[tuple[str, ...]]:
        return [call[0] for call in self.calls]


async def test_ensure_demo_venv_builds_with_absolute_targets_and_stamps_last(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("venv")
    root = workspace.main.root.parent
    root.mkdir()
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    monkeypatch.setenv("META_API_KEY", "test-key")
    for name in [key for key in os.environ if key.startswith("UV_")]:
        monkeypatch.delenv(name)  # a developer's own settings must not reach the assertions
    monkeypatch.setenv("UV_CACHE_DIR", str(root / "uv-cache"))
    for name in ("UV_OVERRIDE", "UV_EXCLUDE", "UV_CONSTRAINT", "UV_NO_SOURCES"):
        monkeypatch.setenv(name, "test-value")
    await workspace.ensure_demo_venv()
    venv, python = root / "demo-venv", root / "demo-venv" / "bin" / "python"
    assert fake.commands == [
        ("uv", "--no-config", "venv", "--quiet", "--python", "3.12", str(venv)),
        (
            "uv",
            "--no-config",
            "pip",
            "install",
            "--quiet",
            "--exclude-newer",
            "14 days",
            "--python",
            str(python),
            "pytest==9.1.1",
        ),
    ]
    assert all(Path(part).is_absolute() for part in (str(venv), str(python)))
    assert (venv / "oversight-stamp").read_text().strip() == "pytest==9.1.1"
    for _, env in fake.calls:
        assert "META_API_KEY" not in env
        assert env["UV_CACHE_DIR"] == str(root / "uv-cache")
        assert sorted(key for key in env if key.startswith("UV_")) == ["UV_CACHE_DIR"]


async def test_ensure_demo_venv_reuses_a_stamped_venv_that_runs(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("reuse")
    root = workspace.main.root.parent
    (root / "demo-venv" / "bin").mkdir(parents=True)
    (root / "demo-venv" / "bin" / "python").write_text("")
    (root / "demo-venv" / "oversight-stamp").write_text("pytest==9.1.1\n")
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    await workspace.ensure_demo_venv()
    assert [argv[0] for argv in fake.commands] == [str(root / "demo-venv" / "bin" / "python")]


@pytest.mark.parametrize("stamp", ["pytest==9.0.0\n", "", None])
async def test_ensure_demo_venv_rebuilds_on_a_wrong_or_missing_stamp(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch, stamp: str | None
) -> None:
    workspace = make_workspace("rebuild")
    root = workspace.main.root.parent
    (root / "demo-venv").mkdir(parents=True)
    (root / "demo-venv" / "leftover.txt").write_text("old")
    if stamp is not None:
        (root / "demo-venv" / "oversight-stamp").write_text(stamp)
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    await workspace.ensure_demo_venv()
    assert not (root / "demo-venv" / "leftover.txt").exists()
    assert [argv[2] for argv in fake.commands] == ["venv", "pip"]


async def test_ensure_demo_venv_rebuilds_when_the_python_does_not_run(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("broken")
    root = workspace.main.root.parent
    (root / "demo-venv" / "bin").mkdir(parents=True)
    (root / "demo-venv" / "bin" / "python").write_text("")
    (root / "demo-venv" / "oversight-stamp").write_text("pytest==9.1.1\n")
    fake = FakeUv(root, probe_ok=False)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    await workspace.ensure_demo_venv()
    assert len(fake.commands) == 3


@pytest.mark.parametrize("damage", ["no interpreter", "dangling symlink"])
async def test_ensure_demo_venv_rebuilds_when_the_python_is_missing_or_a_broken_link(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    workspace = make_workspace("missing")
    root = workspace.main.root.parent
    (root / "demo-venv" / "bin").mkdir(parents=True)
    (root / "demo-venv" / "oversight-stamp").write_text("pytest==9.1.1\n")
    if damage == "dangling symlink":
        (root / "demo-venv" / "bin" / "python").symlink_to(root / "gone" / "python3.12")
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    await workspace.ensure_demo_venv()
    python = str(root / "demo-venv" / "bin" / "python")
    assert [argv[0] for argv in fake.commands] == [python, "uv", "uv"]
    assert [argv[2] for argv in fake.commands[1:]] == ["venv", "pip"]
    assert (root / "demo-venv" / "oversight-stamp").read_text().strip() == "pytest==9.1.1"


@pytest.mark.parametrize("name", ["UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX"])
async def test_a_mirror_chosen_through_the_environment_reaches_uv_and_overrides_do_not(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    workspace = make_workspace("mirror")
    root = workspace.main.root.parent
    root.mkdir()
    fake = FakeUv(root)
    monkeypatch.setattr(workspace_core, "run_process", fake)
    for key in [key for key in os.environ if key.startswith("UV_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv(name, "https://mirror.invalid/simple")
    for denied in ("UV_OVERRIDE", "UV_EXCLUDE", "UV_CONSTRAINT", "UV_EXTRA_INDEX_URL"):
        monkeypatch.setenv(denied, "test-value")
    await workspace.ensure_demo_venv()
    assert len(fake.calls) == 2
    for _, env in fake.calls:
        assert {key: value for key, value in env.items() if key.startswith("UV_")} == {
            name: "https://mirror.invalid/simple"
        }


def test_the_install_passes_the_repositorys_release_age_rule(repo_root: Path) -> None:
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    assert pyproject["tool"]["uv"]["exclude-newer"] == workspace_core.EXCLUDE_NEWER


async def test_a_failed_install_leaves_no_stamp(
    make_workspace: MakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = make_workspace("failed")
    root = workspace.main.root.parent
    root.mkdir()
    monkeypatch.setattr(workspace_core, "run_process", FakeUv(root, install_ok=False))
    with pytest.raises(WorkspaceError, match="uv failed"):
        await workspace.ensure_demo_venv()
    assert not (root / "demo-venv" / "oversight-stamp").exists()


async def test_redact_strips_runtime_paths(make_workspace: MakeWorkspace) -> None:
    workspace = make_workspace("redact")
    root = str(workspace.main.root.parent)
    text = (
        f"{root}/workspace/shop/mod.py:3: error\n"
        f"{root}/worktrees/agent-12/tests/test_x.py::test_a FAILED\n"
        f"File {root}/demo-venv/lib/python3.12/site-packages/_pytest/x.py\n"
        f"cwd {root}/workspace\ncwd {root}/worktrees/agent-1 done\nroot {root}\nplain text"
    )
    redacted = workspace.redact(text)
    assert root not in redacted
    assert redacted.splitlines() == [
        "shop/mod.py:3: error",
        "tests/test_x.py::test_a FAILED",
        "File .runtime/demo-venv/lib/python3.12/site-packages/_pytest/x.py",
        "cwd ",
        "cwd  done",
        "root .runtime",
        "plain text",
    ]


# --- Running the demo repo's tests ------------------------------------------------------------


async def test_run_tests_on_demo_repo_passes(
    make_workspace: MakeWorkspace,
    demo_python: Path,
    git_out: GitOut,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo_root: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = make_workspace("demo", source=repo_root / "demo-repo", demo_python=demo_python)
    await workspace.acquire_runtime()
    await workspace.reset()
    tree = await workspace.create_worktree("agent-1")
    for target in (workspace.main, tree):
        run = await workspace.run_tests(target, timeout_s=120)
        assert isinstance(run.result, SuiteResult)
        assert run.result.outcome == "passed", run.output
        assert run.result.passed > 0
        assert (run.result.failed, run.result.failed_tests) == (0, [])
        assert str(workspace.main.root.parent) not in run.output
        assert "passed" in run.output
        assert (await git_out(target.root, "status", "--porcelain")).strip() == ""
        assert not list(target.root.rglob("__pycache__"))
        assert not (target.root / ".pytest_cache").exists()
    assert list((workspace.main.root.parent / "tmp").glob("*.jsonl")) == []


CASES = (
    "import pytest\n\n\n"
    "def test_ok() -> None:\n    assert True\n\n\n"
    "def test_bad() -> None:\n    assert 1 == 2\n\n\n"
    "@pytest.mark.skip\ndef test_skipped() -> None:\n    pass\n\n\n"
    "@pytest.mark.xfail\ndef test_xfailed() -> None:\n    assert False\n"
)


async def test_run_tests_reports_failures_and_skips(
    make_workspace: MakeWorkspace,
    demo_python: Path,
    tmp_path: Path,
    write_tree_files: Callable[[Path, dict[str, str]], Path],
) -> None:
    source = write_tree_files(tmp_path / "cases-source", {"tests/test_cases.py": CASES})
    workspace = make_workspace("cases", source=source, demo_python=demo_python)
    await workspace.acquire_runtime()
    await workspace.reset()
    run = await workspace.run_tests(workspace.main, timeout_s=120)
    result = run.result
    assert (result.outcome, result.passed, result.failed, result.skipped) == ("failed", 1, 1, 2)
    assert result.failed_tests == ["tests/test_cases.py::test_bad"]
    assert "assert 1 == 2" in run.output


async def test_run_tests_timeout_reports_partial_counts(
    make_workspace: MakeWorkspace,
    demo_python: Path,
    tmp_path: Path,
    write_tree_files: Callable[[Path, dict[str, str]], Path],
    wait_gone: Callable[[int], None],
) -> None:
    pid_file = tmp_path / "hang.pid"
    hang = (
        "import os, time\n\n\ndef test_hang() -> None:\n"
        f"    open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n    time.sleep(600)\n"
    )
    source = write_tree_files(
        tmp_path / "hang-source",
        {
            "tests/test_a_first.py": "def test_first() -> None:\n    assert True\n",
            "tests/test_b_hang.py": hang,
        },
    )
    workspace = make_workspace("hang", source=source, demo_python=demo_python)
    await workspace.acquire_runtime()
    await workspace.reset()
    run = await workspace.run_tests(workspace.main, timeout_s=8)
    assert run.result.outcome == "timeout"
    assert (run.result.passed, run.result.failed) == (1, 0)
    wait_gone(int(pid_file.read_text()))
    assert list((workspace.main.root.parent / "tmp").glob("*.jsonl")) == []
