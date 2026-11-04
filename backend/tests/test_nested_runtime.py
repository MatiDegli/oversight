"""Explicit nested construction, without composing or starting the running application."""

import asyncio
import dataclasses
import inspect
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from oversight.settings import REPO_ROOT, Settings
from oversight.slots import resolve_slots
from oversight.workspace import Workspace, run_process

FILES = REPO_ROOT / "fixtures/fake-model"


def prepared(tmp_path: Path):
    from oversight.nested_runtime import NestedConfig

    tree, preview = tmp_path / "tree", tmp_path / "preview"
    dist, layout = tree / "frontend/dist", tree / "layout.json"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    layout.write_text("{}")
    preview.mkdir()
    return NestedConfig(
        tree=tree,
        preview_dir=preview,
        demo_python=Path(sys.executable),
        coder_script=FILES / "demo-preview-self-nested.json",
        lead_script=FILES / "demo-preview-self-nested.json",
        frontend_dist=dist,
        layout=layout,
        api_port=18101,
        public_port=18101,
    )


def test_nested_factory_requires_explicit_inputs(tmp_path: Path) -> None:
    from oversight.nested_clients import ScriptedFactory
    from oversight.nested_runtime import AppFactory, create_nested_app

    config = prepared(tmp_path)
    factory = Mock(return_value=object())
    result = create_nested_app(config, app_factory=factory)
    assert result is factory.return_value
    factory.assert_called_once()
    inputs = factory.call_args.kwargs
    assert set(inputs) == {"settings", "tree", "policy", "coder_factory", "lead_factory"}
    assert inputs["tree"] == config.tree
    assert isinstance(inputs["settings"], Settings)
    from oversight.slots import resolve_slots

    assert inputs["settings"].max_agents == 1
    assert list(resolve_slots(inputs["settings"])) == ["A"]
    assert inputs["settings"].agent_models == inputs["settings"].fake_scripts == {}
    assert inputs["settings"].runtime_dir == config.preview_dir / "state" / ".runtime"
    assert inputs["policy"].supported_roles == ("coder", "lead")
    assert inputs["coder_factory"] is not inputs["lead_factory"]
    assert list(inspect.signature(AppFactory.__call__).parameters) == [
        "self",
        "settings",
        "tree",
        "policy",
        "coder_factory",
        "lead_factory",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for name, parameter in inspect.signature(AppFactory.__call__).parameters.items()
        if name != "self"
    )
    assert list(inspect.signature(ScriptedFactory.__call__).parameters) == [
        "self",
        "profile",
        "slot",
    ]
    assert (
        inspect.signature(create_nested_app).parameters["app_factory"].default
        is inspect.Parameter.empty
    )
    assert_import_has_no_side_effects(config)
    assert not (config.preview_dir / "state" / ".runtime").exists()


def assert_import_has_no_side_effects(config) -> None:
    # A fresh interpreter proves direct imports/construction do not import oversight.app.
    code = """
import importlib.abc
import sys
from pathlib import Path
from unittest.mock import Mock
class NoApp(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'oversight.app':
            raise AssertionError('running app imported')
sys.meta_path.insert(0, NoApp())
import oversight.settings as settings
import oversight.workspace as workspace
def denied(*args, **kwargs):
    raise AssertionError('environment or process access')
settings.Settings.load = denied
settings.read_env_file = denied
workspace.run_process = denied
sys.modules['oversight.workspace.runner'].run_process = denied
original_open = Path.open
def prepared_files_only(path, *args, **kwargs):
    if path.name == '.env' or path.name.startswith('.env.'):
        raise AssertionError('environment file opened')
    return original_open(path, *args, **kwargs)
Path.open = prepared_files_only
from oversight.nested_runtime import NestedConfig, create_nested_app
from oversight.nested_clients import ScriptedFactory
config = NestedConfig(**{k: Path(v) if k not in ('api_port', 'public_port') else v
                         for k, v in CONFIG.items()})
factory = Mock(return_value='injected')
assert create_nested_app(config, app_factory=factory) == 'injected'
assert 'oversight.app' not in sys.modules
assert len(factory.call_args.kwargs) == 5
"""
    values = dataclasses.asdict(config)
    values = {
        key: str(value) if isinstance(value, Path) else value for key, value in values.items()
    }
    result = asyncio.run(
        run_process(
            [sys.executable, "-c", f"CONFIG = {values!r}\n" + code],
            cwd=config.tree,
            timeout_s=30,
        )
    )
    assert result.returncode == 0, result.stderr
    assert not result.timed_out and not result.truncated


def test_nested_settings_are_explicit_and_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oversight.nested_runtime import build_nested_settings

    config = prepared(tmp_path)
    denied = Mock(side_effect=AssertionError("environment loader or process used"))
    monkeypatch.setattr(Settings, "load", denied)
    monkeypatch.setattr("oversight.settings.read_env_file", denied)
    monkeypatch.setattr("oversight.workspace.run_process", denied)
    monkeypatch.setattr("oversight.workspace.core.run_process", denied)
    for key, value in {
        "META_API_KEY": "sentinel-meta",
        "BASETEN_API_KEY": "sentinel-baseten",
        "OVERSIGHT_RUNTIME_DIR": str(config.tree / "bad-state"),
        "OVERSIGHT_AGENT_MODEL": "muse-spark",
        "OVERSIGHT_CROSS_REVIEW": "true",
        "OVERSIGHT_PREVIEW_ENABLED": "true",
        "OVERSIGHT_PORT": "1",
        "OVERSIGHT_VITE_PORT": "2",
        "OVERSIGHT_DEMO_PYTHON": "/unprepared/python",
    }.items():
        monkeypatch.setenv(key, value)
    settings = build_nested_settings(config)
    assert settings.runtime_dir == config.preview_dir / "state" / ".runtime"
    assert not settings.runtime_dir.is_relative_to(config.tree)
    assert (settings.port, settings.vite_port) == (18101, None)
    assert settings.demo_python == config.demo_python  # Preserve the venv path, not its target.
    assert settings.layout == config.layout and settings.frontend_dist == config.frontend_dist
    assert settings.meta_api_key is settings.baseten_api_key is None
    assert {slot.profile.provider for slot in resolve_slots(settings).values()} == {"fake"}
    assert not settings.cross_review and not settings.behavior_diff and not settings.measured_cone
    assert settings.review_card_classifications == 0
    assert settings.review_model == settings.decision_model == "fake"
    assert settings.budget_usd is settings.baseten_budget_usd is None
    workspace = Workspace(
        settings.runtime_dir,
        config.tree,
        pytest_pin="9.1.1",
        demo_python=settings.demo_python,
    )
    asyncio.run(workspace.ensure_demo_venv())
    denied.assert_not_called()
    assert not (settings.runtime_dir / "demo-venv").exists()
    assert not (config.preview_dir / ".runtime/demo-venv").exists()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cast(Any, config).api_port = 1


def test_nested_workspace_opens_state_runtime(tmp_path: Path) -> None:
    from oversight.nested_runtime import build_nested_settings

    config = prepared(tmp_path)
    settings = build_nested_settings(config)
    workspace = Workspace(
        settings.runtime_dir, config.tree, pytest_pin="9.1.1", demo_python=settings.demo_python
    )
    assert workspace.runtime_root == config.preview_dir / "state" / ".runtime"


@pytest.mark.parametrize("field", ["api_port", "public_port"])
@pytest.mark.parametrize("value", [None, True, 0, 65536, "18101"])
def test_ports_are_explicit_assignments(tmp_path: Path, field: str, value) -> None:
    from oversight.nested_runtime import build_nested_settings

    with pytest.raises(ValueError, match="assigned"):
        build_nested_settings(dataclasses.replace(prepared(tmp_path), **{field: value}))


def test_private_state_alias_and_split_ports_refuse(tmp_path: Path) -> None:
    from oversight.nested_runtime import build_nested_settings

    config = prepared(tmp_path)
    with pytest.raises(ValueError, match="one listener"):
        build_nested_settings(dataclasses.replace(config, public_port=config.api_port + 1))
    (config.preview_dir / "state").symlink_to(config.tree, target_is_directory=True)
    with pytest.raises(ValueError, match="private"):
        build_nested_settings(config)


def test_missing_lead_and_assets_refuse_before_factory(tmp_path: Path) -> None:
    from oversight.nested_runtime import create_nested_app

    config = prepared(tmp_path)
    factory = Mock()
    with pytest.raises(ValueError, match="separate scripted lead"):
        create_nested_app(
            dataclasses.replace(config, lead_script=FILES / "demo-preview-self-outer.json"),
            app_factory=factory,
        )
    (config.frontend_dist / "index.html").unlink()
    with pytest.raises(ValueError, match="prepared frontend"):
        create_nested_app(config, app_factory=factory)
    factory.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "tree",
        "preview_dir",
        "demo_python",
        "coder_script",
        "lead_script",
        "frontend_dist",
        "layout",
    ],
)
def test_missing_prepared_inputs_refuse(tmp_path: Path, field: str) -> None:
    from oversight.nested_runtime import build_nested_settings

    config = prepared(tmp_path)
    with pytest.raises(ValueError, match="prepared"):
        build_nested_settings(dataclasses.replace(config, **{field: tmp_path / "missing"}))


@pytest.mark.parametrize("placement", ["inside", "parent", "symlink", "relative"])
def test_nested_state_must_be_separate(tmp_path: Path, placement: str) -> None:
    from oversight.nested_runtime import build_nested_settings

    config = prepared(tmp_path)
    preview = config.tree / "preview"
    if placement == "inside":
        preview.mkdir()
    elif placement == "parent":
        preview = tmp_path
    elif placement == "symlink":
        preview.symlink_to(config.tree, target_is_directory=True)
    else:
        preview = Path("relative")
    with pytest.raises(ValueError):
        build_nested_settings(dataclasses.replace(config, preview_dir=preview))


def test_nested_policy_refuses_further_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oversight.nested_clients import NestedPolicy
    from oversight.nested_runtime import create_nested_app

    monkeypatch.setenv("OVERSIGHT_PREVIEW_DEPTH", "0")
    policy = NestedPolicy()
    for _ in range(3):
        with pytest.raises(ValueError, match="Further managed previews are disabled"):
            policy.require_managed_preview()
    assert not policy.managed_preview_enabled
    with pytest.raises(TypeError):
        NestedPolicy(**{"managed_preview_enabled": True})
    factory = Mock()
    create_nested_app(prepared(tmp_path), app_factory=factory)
    inputs = factory.call_args.kwargs
    profile = inputs["settings"].preview_profile
    assert not profile.enabled and not profile.configured and not profile.trusted_demo
    assert profile.ports == profile.origins == ()
    assert profile.adapter is profile.interpreter is None
    assert set(inputs) == {"tree", "settings", "policy", "coder_factory", "lead_factory"}
    assert not any(
        "token" in field.name or "listener" in field.name or "descriptor" in field.name
        for field in dataclasses.fields(policy)
    )
