"""Installed roots, configuration precedence and the person-owned interpreter option."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from oversight import __main__ as cli
from oversight import settings as configuration
from oversight.settings import Settings
from oversight.workspace import run_process


def test_environment_file_selection_and_explicit_argument_precedence(tmp_path, monkeypatch):
    selected, explicit = tmp_path / "selected.env", tmp_path / "explicit.env"
    selected.write_text("OVERSIGHT_MAX_AGENTS=3\nOVERSIGHT_ENV_FILE=ignored.env\n")
    explicit.write_text("OVERSIGHT_MAX_AGENTS=5\n")
    monkeypatch.delenv("OVERSIGHT_MAX_AGENTS", raising=False)
    monkeypatch.setenv("OVERSIGHT_ENV_FILE", str(selected))
    assert Settings.load().max_agents == 3
    assert Settings.load(explicit).max_agents == 5
    monkeypatch.setenv("OVERSIGHT_MAX_AGENTS", "2")
    assert Settings.load().max_agents == Settings.load(explicit).max_agents == 2


def test_application_root_is_process_only_and_absolute(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERSIGHT_APP_ROOT", raising=False)
    assert configuration._application_root() == Path(configuration.__file__).resolve().parents[3]
    monkeypatch.setenv("OVERSIGHT_APP_ROOT", str(tmp_path))
    assert configuration._application_root() == tmp_path
    monkeypatch.setenv("OVERSIGHT_APP_ROOT", "relative")
    with pytest.raises(ValueError, match="must be absolute"):
        configuration._application_root()


@pytest.mark.anyio
async def test_installed_root_reaches_module_constants_and_boundary(tmp_path):
    code = """
import os
from pathlib import Path
from oversight.settings import REPO_ROOT, Settings
from oversight.app import TEMPLATE, VENV_REQUIREMENTS
assert REPO_ROOT == Path(os.environ['OVERSIGHT_APP_ROOT'])
assert TEMPLATE == REPO_ROOT / 'demo-repo'
assert VENV_REQUIREMENTS == REPO_ROOT / 'tools/demo-venv-requirements.txt'
assert Settings.load().frontend_dist == REPO_ROOT / 'frontend/dist'
if os.name != 'nt':
    from oversight.workspace.preview_boundary import REPO_ROOT as boundary_root
    assert boundary_root == REPO_ROOT
"""
    result = await run_process(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        timeout_s=30,
        env={
            "OVERSIGHT_APP_ROOT": str(tmp_path),
            "OVERSIGHT_ENV_FILE": str(tmp_path / "absent.env"),
            "OVERSIGHT_AGENT_MODEL": "fake",
            "META_API_KEY": "",
            "BASETEN_API_KEY": "",
        },
    )
    assert result.returncode == 0, result.stderr


def test_serve_passes_prepared_interpreter_in_settings(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_bytes(b"fixture")
    monkeypatch.setattr(sys, "argv", ["oversight", "serve", "--demo-python", str(python)])
    monkeypatch.setattr("oversight.__main__.Settings.load", lambda: Settings(agent_model="fake"))
    compose, serve = Mock(return_value=object()), Mock()
    monkeypatch.setattr(cli, "create_app", compose)
    monkeypatch.setattr("oversight.__main__.uvicorn.run", serve)
    cli.main()
    assert compose.call_args.args[0].demo_python == python
    assert serve.call_args.kwargs["factory"] is False
    assert serve.call_args.args[0] is compose.return_value


@pytest.mark.parametrize("args", [["relative.exe"], ["missing.exe"], ["--reload"]])
def test_serve_refuses_invalid_explicit_interpreters(args, tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_bytes(b"fixture")
    option = [str(python), "--reload"] if args == ["--reload"] else args
    monkeypatch.setattr(sys, "argv", ["oversight", "serve", "--demo-python", *option])
    monkeypatch.setattr(cli, "create_app", Mock(side_effect=AssertionError("must refuse first")))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_configuration_cannot_select_the_prepared_interpreter(tmp_path, monkeypatch):
    file = tmp_path / "config.env"
    file.write_text("OVERSIGHT_DEMO_PYTHON=untrusted.exe\n")
    monkeypatch.setenv("OVERSIGHT_DEMO_PYTHON", str(tmp_path / "another.exe"))
    assert Settings.load(file).demo_python is None
