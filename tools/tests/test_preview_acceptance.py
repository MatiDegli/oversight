"""Driver bookkeeping: failures reset consecutive starts and preserve prior evidence."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import preview_acceptance as driver
from tools import preview_browser
from tools.preview_acceptance_checks import settings

from oversight.workspace import IsolationUnavailable

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("failure", [None, "story", "cleanup", "isolation"])
async def test_three_start_bookkeeping_never_converts_failure_into_acceptance(
    tmp_path, monkeypatch, failure
):
    starts = []

    @asynccontextmanager
    async def server(config):
        starts.append(config)
        if failure == "isolation":
            raise IsolationUnavailable("direct preflight refusal")
        yield SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(live=SimpleNamespace(workspace=object()))),
            witnesses=[],
        )

    async def story(running):
        if failure == "story" and len(starts) == 2:
            raise RuntimeError("scripted failure")
        return 1500, 1350

    async def released(workspace, ports):
        if failure == "cleanup" and len(starts) == 2:
            raise RuntimeError("listener remains owned")

    monkeypatch.setattr(driver, "server", server)
    monkeypatch.setattr(driver, "story", story)
    monkeypatch.setattr(driver, "released", released)
    if failure in {"story", "cleanup"}:
        with pytest.raises(RuntimeError):
            await driver.execute(tmp_path, 3)
    else:
        assert await driver.execute(tmp_path, 3) == (2 if failure else 0)
    result = json.loads((tmp_path / "starts.json").read_text())
    assert result["clean"] == (0 if failure else 3)
    if failure in {"story", "cleanup"}:
        assert result["attempts"][0]["consecutive_clean"] == 1
        assert result["attempts"][-1]["consecutive_clean"] == 0
    assert all(config.meta_api_key is None and config.baseten_api_key is None for config in starts)
    assert all(
        config.agent_model == "fake" and config.runtime_dir.name == ".runtime" for config in starts
    )
    assert len({config.runtime_dir for config in starts}) == len(starts)


async def test_browser_driver_selects_explicit_real_c_project(tmp_path, monkeypatch):
    @asynccontextmanager
    async def server(config):
        yield object()

    async def candidate(running):
        return SimpleNamespace(opening=lambda: {})

    class BrowserInvocation(Exception):
        pass

    async def bounded(argv, env, seconds):
        assert argv[argv.index("--project") + 1] == "preview-acceptance"
        assert argv[-1] == "src/app/preview/PreviewAcceptance.browser.test.ts"
        assert await asyncio.to_thread(Path(env["OVERSIGHT_PREVIEW_ACCEPTANCE_CANDIDATE"]).is_file)
        raise BrowserInvocation

    monkeypatch.setattr(preview_browser, "server", server)
    monkeypatch.setattr(preview_browser, "candidate", candidate)
    monkeypatch.setattr(preview_browser, "bounded", bounded)
    with pytest.raises(BrowserInvocation):
        await preview_browser.execute(tmp_path)


async def test_acceptance_settings_explicitly_enable_trusted_profile(tmp_path):
    from oversight.workspace import PreviewProfile

    config = settings(tmp_path / ".runtime", 18710, vite=15710, pool=(19901, 19902))
    assert config.preview_profile.enabled is True
    assert config.preview_profile.production_open is True
    assert config.preview_profile.trusted_demo is True
    config.preview_profile.validate()
    assert PreviewProfile().enabled is False
