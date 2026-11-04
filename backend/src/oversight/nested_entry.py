"""Dedicated, explicit scripted nested startup; no ordinary CLI or environment loading."""

import argparse
import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from oversight.app import create_app
from oversight.nested_clients import NestedPolicy, ScriptedFactory
from oversight.nested_runtime import NestedConfig, create_nested_app
from oversight.settings import Settings
from oversight.workspace import PreviewError, PreviewManager, Workspace


class NestedPreviews(PreviewManager):
    """Local refusal is independent of depth inputs and precedes manager reservation."""

    def __init__(self, workspace: Workspace, settings: Settings, policy: NestedPolicy) -> None:
        self.policy = policy
        super().__init__(workspace, settings.preview_profile)

    def _admit(self) -> None:
        try:
            self.policy.require_managed_preview()
        except ValueError as error:
            raise PreviewError(str(error), code="not_configured") from None


def compose_app(
    *,
    settings: Settings,
    tree: Path,
    policy: NestedPolicy,
    coder_factory: ScriptedFactory,
    lead_factory: ScriptedFactory,
) -> FastAPI:
    """Adapt the landed injected interface to real app composition with explicit inputs."""
    if not (tree / "demo-repo").is_dir():
        raise ValueError("Nested composition requires the disposable tree's demo-repo")
    return create_app(
        settings,
        tree=tree,
        policy=policy,
        client_factory=coder_factory,
        lead_factory=lead_factory,
        preview_factory=lambda workspace: NestedPreviews(workspace, settings, policy),
    )


def parse_config(argv: list[str] | None = None) -> NestedConfig:
    """Require owner-supplied paths and ports; derive only the fixed prepared inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tree", "state", "demo-python"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("api-port", "public-port"):
        parser.add_argument(f"--{name}", type=int, required=True)
    args = parser.parse_args(argv)
    tree: Path = args.tree
    state: Path = args.state
    if not state.is_absolute() or state.name != "state":
        raise ValueError("Nested state must be an explicit absolute directory named state")
    story = tree / "fixtures/fake-model/demo-preview-self-nested.json"
    return NestedConfig(
        tree=tree,
        preview_dir=state.parent,
        demo_python=args.demo_python,
        coder_script=story,
        lead_script=story,
        frontend_dist=state / "dist",
        layout=tree / "fixtures/layouts/demo-repo.json",
        api_port=args.api_port,
        public_port=args.public_port,
    )


async def serve(config: NestedConfig) -> None:
    """API and prepared static assets share the assigned loopback listener."""
    app = create_nested_app(config, app_factory=compose_app)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=config.api_port, proxy_headers=False)
    )
    await server.serve()


def main(argv: list[str] | None = None) -> None:
    asyncio.run(serve(parse_config(argv)))


if __name__ == "__main__":
    main()
