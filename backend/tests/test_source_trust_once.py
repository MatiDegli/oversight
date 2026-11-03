"""Trust is informational, bounded and keyed independently of reset/discard authority."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from live_helpers import live_settings

from oversight import app
from oversight.workspace import TRUST_NOTICE, NtSource, outside_profile, remember_trust


def test_marker_keys_and_unsafe_metadata(tmp_path: Path):
    source = NtSource(tmp_path / "source", tmp_path, r"C:\private\source", r"C:\runtime")
    assert remember_trust(source, ("fake",), False) is False
    assert remember_trust(source, ("fake", "fake"), False) is True
    assert remember_trust(source, ("meta",), False) is False
    assert remember_trust(source, ("meta",), True) is False
    assert remember_trust(source, ("meta",), True, policy="next-policy") is False
    assert (
        remember_trust(replace(source, source_path=r"C:\private\other"), ("meta",), True) is False
    )
    marker = tmp_path / "own-trust"
    assert not (tmp_path / "own-runtime").exists() and not (tmp_path / "own-source").exists()
    marker.unlink()
    target = tmp_path / "unowned"
    target.write_bytes(b"never overwrite")
    marker.symlink_to(target)
    assert not remember_trust(source, ("fake",), False)
    assert target.read_bytes() == b"never overwrite" and marker.is_symlink()


def test_marker_sorted_union_canonical_path_and_outside_profile(tmp_path):
    source = NtSource(tmp_path, tmp_path, r"C:\Users\Person\source", r"C:\runtime")
    assert not outside_profile(source, r"C:\Users\Person")
    assert outside_profile(source, r"D:\Users\Person") and outside_profile(source, None)
    assert not remember_trust(source, ("meta", "baseten"), False)
    assert remember_trust(
        replace(source, source_path=r"c:\users\person\source"), ("baseten", "meta"), False
    )


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"agent_model": "muse-spark"},
        {"cross_review": True, "review_model": "muse-spark-open"},
        {"decision_model": "muse-spark-open"},
        {"decision_model": "unavailable", "cross_review": True, "review_model": "unknown"},
    ],
)
def test_provider_union_covers_offered_profiles_and_both_callable_leads(
    tmp_path, monkeypatch, config
):
    def forbidden(*args, **kwargs):
        pytest.fail("provider inventory constructed a client or probed a provider")

    monkeypatch.setattr(app, "OpenAIClient", forbidden)
    live = app.LiveRun(live_settings(tmp_path, **config))
    providers = live._source_providers()
    assert providers == (
        ("baseten", "fake", "meta")
        if config.get("agent_model", "fake") == "fake"
        else ("baseten", "meta")
    )
    assert live._settings.meta_api_key is live._settings.baseten_api_key is None


@pytest.mark.anyio
async def test_detail_once_but_provider_and_per_start_disclosure_repeat(
    tmp_path, monkeypatch, caplog
):
    live = app.LiveRun(live_settings(tmp_path))
    live._own_selection = NtSource(tmp_path, tmp_path, r"C:\private\source", r"C:\runtime")
    live.workspace._own_source = live._own_selection
    reset = AsyncMock()
    monkeypatch.setattr(live.workspace, "reset", reset)
    for _ in range(2):
        await live._disclose_source()
    assert caplog.messages.count(TRUST_NOTICE) == 1
    assert sum(m.startswith("Own-source provider destinations:") for m in caplog.messages) == 2
    assert not any("all fake" in m or "no model sends" in m for m in caplog.messages)
    assert (tmp_path / "own-trust").exists()
    # A notice marker has no authority over the prior-runtime guard or reset.
    refusal = AsyncMock(side_effect=OSError("runtime preservation required"))
    monkeypatch.setattr(live.workspace, "guard_previous_runtime", refusal)
    with pytest.raises(OSError, match="preservation required"):
        await live._reset_workspace()
    reset.assert_not_awaited()
