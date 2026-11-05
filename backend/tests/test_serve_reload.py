"""`serve --reload` watches only the oversight package, never the runtime worktrees."""

import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from live_helpers import live_settings

import oversight
from oversight import __main__ as cli
from oversight.settings import Settings


@pytest.mark.parametrize("reload", [True, False])
def test_serve_reload_watches_only_the_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload: bool
) -> None:
    calls: list[dict[str, Any]] = []
    settings = live_settings(tmp_path)
    monkeypatch.setattr(Settings, "load", staticmethod(lambda: settings))
    monkeypatch.setattr(uvicorn, "run", lambda *_, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, "argv", ["oversight", "serve", *(["--reload"] if reload else [])])
    cli.main()
    [kwargs] = calls
    assert kwargs["reload"] is reload
    if reload:
        assert kwargs["reload_dirs"] == [str(Path(oversight.__file__).resolve().parent)]
    else:
        assert kwargs["reload_dirs"] is None
