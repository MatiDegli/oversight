"""Independent explicit nested configuration; importing this module never composes the app."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from oversight.nested_clients import (
    NestedPolicy,
    ScriptedFactory,
    load_story,
    make_coder_factory,
    make_lead_factory,
)
from oversight.settings import Settings
from oversight.workspace import PreviewProfile


@dataclass(frozen=True)
class NestedConfig:
    """Prepared paths and assigned ports supplied by the owner, never by environment loading.

    The interpreter path retains its venv identity. Asset/layout paths must already exist;
    dependency identity, read-only mounts and production import enforcement belong to the caller.
    Only this preview's child state belongs under preview_dir/state, outside the tested tree.
    """

    tree: Path
    preview_dir: Path
    demo_python: Path
    coder_script: Path
    lead_script: Path
    frontend_dist: Path
    layout: Path
    api_port: int
    public_port: int


class AppFactory[AppT](Protocol):
    """Mandatory structural injection; no web framework or default application import."""

    def __call__(
        self,
        *,
        settings: Settings,
        tree: Path,
        policy: NestedPolicy,
        coder_factory: ScriptedFactory,
        lead_factory: ScriptedFactory,
    ) -> AppT: ...


def _validate_prepared(config: NestedConfig) -> None:
    directories = (config.tree, config.preview_dir, config.frontend_dist)
    files = (config.demo_python, config.coder_script, config.lead_script, config.layout)
    if any(not path.is_absolute() or not path.is_dir() for path in directories) or any(
        not path.is_absolute() or not path.is_file() for path in files
    ):
        raise ValueError("Nested configuration requires existing absolute prepared inputs")
    if not os.access(config.demo_python, os.X_OK):
        raise ValueError("Nested configuration requires an executable prepared interpreter")
    tree, preview = config.tree.resolve(), config.preview_dir.resolve()
    if preview.is_relative_to(tree) or tree.is_relative_to(preview):
        raise ValueError("Nested preview state must be separate from the disposable tree")
    state = config.preview_dir / "state"
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise ValueError("Nested preview state requires a private prepared directory")
    if any(
        type(port) is not int or not 1 <= port <= 65535
        for port in (config.api_port, config.public_port)
    ):
        raise ValueError("Nested configuration requires assigned API and public ports")
    if not (config.frontend_dist / "index.html").is_file():
        raise ValueError("Nested configuration requires prepared frontend assets")


def build_nested_settings(config: NestedConfig) -> Settings:
    """Build private settings directly: no .env, inherited keys, installs or environment setup."""
    _validate_prepared(config)
    return Settings(
        runtime_dir=config.preview_dir / "state",
        host="127.0.0.1",
        port=config.api_port,
        vite_port=config.public_port,
        demo_python=config.demo_python,
        layout=config.layout,
        frontend_dist=config.frontend_dist,
        meta_api_key=None,
        baseten_api_key=None,
        max_agents=1,
        agent_model="fake",
        fake_script=config.coder_script,
        agent_model_b="fake-b",
        agent_model_c="fake",
        agent_model_d="fake",
        agent_model_e="fake",
        agent_model_f="fake",
        cross_review=False,
        review_model="fake",
        decision_model="fake",
        review_card_classifications=0,
        behavior_diff=False,
        measured_cone=False,
        lead_wakes=0,
        preview_profile=PreviewProfile(enabled=False, trusted_demo=False),
    )


def create_nested_app[AppT](config: NestedConfig, *, app_factory: AppFactory[AppT]) -> AppT:
    """Pass the disposable tree explicitly; only the injected caller can compose an application."""
    settings = build_nested_settings(config)
    policy = NestedPolicy()
    coder = load_story(config.coder_script).coder
    lead = load_story(config.lead_script).lead
    if lead is None:
        raise ValueError("Nested configuration requires a separate scripted lead")
    return app_factory(
        settings=settings,
        tree=config.tree,
        policy=policy,
        coder_factory=make_coder_factory(coder, policy=policy),
        lead_factory=make_lead_factory(lead, policy=policy),
    )
