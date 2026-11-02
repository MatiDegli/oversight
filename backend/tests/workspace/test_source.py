"""Pinned sources use the existing copy, Git and suite boundaries, without network access."""

import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from oversight.settings import Settings
from oversight.workspace import Workspace, run_process
from oversight.workspace.core import INITIAL_DATE, PLUGIN_DIR
from oversight.workspace.runner import ProcessResult
from oversight.workspace.source import BOLTONS, select_source, validate_source

pytestmark = pytest.mark.anyio


def outside_tree(root: Path) -> Path:
    files = {
        "boltons/__init__.py": "",
        "boltons/value.py": (
            'def value():\n    """\n    >>> value()\n    4...\n    """\n    return 42\n'
        ),
        "tests/test_value.py": (
            "from boltons.value import value\ndef test_value():\n    assert value() == 42\n"
        ),
        "docs/conf.py": "def configure():\n    return 1\n",
        "docs/test_ignored.py": "def test_not_collected():\n    assert False\n",
        "pyproject.toml": '[tool.pytest.ini_options]\ndoctest_optionflags = ["ELLIPSIS"]\n',
        "LICENSE": "BSD-3-Clause fixture\n",
    }
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return root


async def git(root: Path, *args: str) -> str:
    result = await run_process(["git", *args], cwd=root, timeout_s=10)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


async def test_outside_reset_preserves_source_and_fixed_initial_commit(tmp_path: Path) -> None:
    source = outside_tree(tmp_path / "source")
    await git(source, "init", "-q")
    await git(source, "config", "outside.fixture", "must-not-copy")
    (source / "boltons/__pycache__").mkdir()
    (source / "boltons/__pycache__/value.pyc").write_bytes(b"cache")
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    workspace = Workspace(tmp_path / ".runtime", source, pytest_pin="9.1.1")
    await workspace.acquire_runtime()
    try:
        base = await workspace.reset()
        main = workspace.main.root
        assert await git(main, "branch", "--show-current") == "main"
        assert await git(main, "rev-list", "--count", "HEAD") == "1"
        log = await git(main, "log", "--format=%an <%ae>|%cn <%ce>|%aI|%cI|%s")
        author, committer, authored, committed, message = log.split("|")
        assert author == committer == "Oversight <oversight@oversight.invalid>"
        # Git versions format UTC as either Z or +00:00; compare the recorded instant.
        assert datetime.fromisoformat(authored) == datetime.fromisoformat(INITIAL_DATE)
        assert datetime.fromisoformat(committed) == datetime.fromisoformat(INITIAL_DATE)
        assert message == "Initial workspace"
        assert await git(main, "rev-parse", "HEAD") == base
        assert await git(main, "status", "--porcelain") == ""
        assert not (main / "boltons/__pycache__").exists()
        for name in ("LICENSE", "pyproject.toml", "boltons/value.py", "tests/test_value.py"):
            assert (main / name).read_bytes() == (source / name).read_bytes()
        assert (main / ".git").is_dir()
        assert (main / ".git/config").read_bytes() != (source / ".git/config").read_bytes()
        assert before == {
            str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()
        }
    finally:
        workspace.release_runtime()


@pytest.mark.parametrize("value", ["", "boltons-d31d821", "../source", "boltons"])
async def test_profile_selection(value: str) -> None:
    if value == "":
        assert select_source(value) is None
    elif value == BOLTONS.name:
        assert select_source(value) == BOLTONS
        assert BOLTONS.extra_requirements == ()
        with pytest.raises(ValueError, match="environment builder"):
            replace(BOLTONS, extra_requirements=("invented==1",))
    else:
        with pytest.raises(ValueError, match=r"OVERSIGHT_WORKSPACE_SOURCE.*boltons-d31d821"):
            select_source(value)


@pytest.mark.parametrize("value", [None, "", "boltons-d31d821"])
async def test_source_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    monkeypatch.delenv("OVERSIGHT_WORKSPACE_SOURCE", raising=False)
    assert Settings.load(tmp_path / "missing").workspace_source == ""
    env = tmp_path / ".env"
    env.write_text("OVERSIGHT_WORKSPACE_SOURCE=boltons-d31d821\n")
    assert Settings.load(env).workspace_source == BOLTONS.name
    if value is not None:
        monkeypatch.setenv("OVERSIGHT_WORKSPACE_SOURCE", value)
    assert Settings.load(tmp_path / "missing").workspace_source == (value or "")


async def test_source_validation_missing_wrong_pin_and_dirty(tmp_path: Path) -> None:
    runtime = tmp_path / ".runtime"
    with pytest.raises(ValueError, match="make workspace-fetch-boltons"):
        await validate_source(BOLTONS, runtime)
    root = outside_tree(BOLTONS.checkout(runtime))
    await git(root, "init", "-q")
    await git(root, "add", ".")
    await git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "Fixture",
    )
    with pytest.raises(ValueError, match="make workspace-fetch-boltons"):
        await validate_source(BOLTONS, runtime)
    profile = replace(BOLTONS, commit=await git(root, "rev-parse", "HEAD"))
    assert await validate_source(profile, runtime) == root
    (root / "LICENSE").write_text("dirty")
    with pytest.raises(ValueError, match="dirty"):
        await validate_source(profile, runtime)


async def test_profile_reuses_demo_environment_stamp_without_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / ".runtime"
    venv = runtime / "demo-venv"
    venv.mkdir(parents=True)
    stamp = venv / "oversight-stamp"
    stamp.write_text("pytest==9.1.1\n")
    workspace = Workspace(
        runtime,
        BOLTONS.checkout(runtime),
        pytest_pin="9.1.1",
        pytest_targets=BOLTONS.pytest_targets,
        pytest_options=BOLTONS.pytest_options,
    )
    calls: list[tuple[str, ...]] = []

    async def probe(argv: Sequence[str], **kwargs: object) -> ProcessResult:
        calls.append(tuple(argv))
        assert tuple(argv) == (str(venv / "bin/python"), "-c", "import pytest")
        return ProcessResult(0, "", "", False, False, 1)

    monkeypatch.setattr("oversight.workspace.core.run_process", probe)
    await workspace.ensure_demo_venv()
    assert len(calls) == 1
    assert stamp.read_text() == "pytest==9.1.1\n"


async def test_source_validation_refuses_symlink(tmp_path: Path) -> None:
    runtime = tmp_path / ".runtime"
    root = BOLTONS.checkout(runtime)
    root.parent.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".git").mkdir(parents=True)
    root.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe checkout"):
        await validate_source(BOLTONS, runtime)


@pytest.mark.parametrize(
    "case_ids",
    [(), ("tests/test_value.py::test_value",), ("boltons/value.py::boltons.value.value",)],
)
async def test_source_suite_argv_and_collection(tmp_path: Path, case_ids: tuple[str, ...]) -> None:
    source = outside_tree(tmp_path / "source")
    workspace = Workspace(
        tmp_path / ".runtime",
        source,
        pytest_pin="9.1.1",
        demo_python=Path(sys.executable),
        pytest_targets=BOLTONS.pytest_targets,
        pytest_options=BOLTONS.pytest_options,
    )
    calls: list[tuple[str, ...]] = []

    async def spawn(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = 262144,
    ) -> ProcessResult:
        calls.append(tuple(argv))
        assert env is not None
        assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert env["PYTHONPATH"] == str(PLUGIN_DIR)
        assert cwd == workspace.main.root
        return await run_process(argv, cwd=cwd, timeout_s=timeout_s, env=env, output_cap=output_cap)

    await workspace.acquire_runtime()
    try:
        await workspace.reset()
        await workspace.ensure_demo_venv()  # injected interpreter: no builder
        run = await workspace.run_tests(
            workspace.main, timeout_s=20, case_ids=case_ids, spawn=spawn
        )
        assert run.result.outcome == "passed", run.output
        assert run.result.passed == (1 if case_ids else 2)
        [argv] = calls
        assert argv[:11] == (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--color=no",
            "-p",
            "no:cacheprovider",
            "-p",
            "oversight_report",
            "-o",
            "junit_family=xunit1",
        )
        assert argv[11].startswith("--junitxml=")
        assert argv[12:] == (
            *BOLTONS.pytest_options,
            *(("--", *case_ids) if case_ids else BOLTONS.pytest_targets),
        )
    finally:
        workspace.release_runtime()


@pytest.mark.parametrize(
    "path",
    [
        r"C:\private\project",
        "C:/private/project",
        r"\\host\share\project",
        r"\\?\C:\project",
        "C:project",
    ],
)
async def test_public_nt_paths_remain_closed(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oversight.workspace.source.sys.platform", "win32")
    with pytest.raises(ValueError, match="OVERSIGHT_WORKSPACE_SOURCE"):
        select_source(path)
    assert select_source("") is None and select_source(BOLTONS.name) == BOLTONS
