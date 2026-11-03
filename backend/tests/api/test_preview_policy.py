"""Exact application-port framing and privacy before routing; no Origin or proxy widening."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from oversight.api.errors import install_error_handlers
from oversight.api.preview_policy import frame_policy
from oversight.api.static import add_static
from oversight.settings import Settings, resolve_preview_profile


@pytest.mark.parametrize("pool", [(), (19681,), (19681, 19682)])
def test_only_explicit_app_ports_are_frame_destinations(pool):
    expected = (
        "frame-src " + " ".join(f"http://127.0.0.1:{p}" for p in pool)
        if pool
        else "frame-src 'none'"
    )
    assert frame_policy(pool) == expected
    assert frame_policy(pool, replay=True) == "frame-src 'none'"


@pytest.mark.parametrize("pool", [(0,), (65536,), (True,), (19681, 19681)])
def test_invalid_port_pools_do_not_make_wildcard_policy(pool):
    with pytest.raises(ValueError):
        frame_policy(pool)


@pytest.mark.anyio
async def test_static_html_and_spa_receive_pool_policy_without_changing_assets(tmp_path):
    from oversight.workspace import PreviewProfile

    (tmp_path / "index.html").write_text("<!doctype html><html><head></head><body>UI</body></html>")
    (tmp_path / "asset.js").write_text("export {}")
    app = FastAPI()
    app.state.settings = Settings(
        preview_profile=PreviewProfile(ports=(19681, 19682), origins=("http://127.0.0.1:8710",))
    )
    install_error_handlers(app)
    assert add_static(app, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8710"
    ) as client:
        for path in ("/", "/nested/spa", "/index.html"):
            response = await client.get(path)
            assert (
                response.headers["content-security-policy"]
                == "frame-src http://127.0.0.1:19681 http://127.0.0.1:19682"
            )
            assert response.headers["cache-control"] == "no-cache"
            assert "8710" not in response.headers["content-security-policy"]
        assert "content-security-policy" not in (await client.get("/asset.js")).headers


def test_documented_configuration_and_reason_inventory_are_exact():
    from typing import get_args

    from oversight_contracts.previews import PreviewReason

    docs = (Path(__file__).resolve().parents[3] / "docs/isolation.md").read_text()
    defaults = {
        "ADAPTER": None,
        "PYTHON": None,
        "PORTS": (),
        "ORIGINS": (),
        "HEARTBEAT_S": 10,
        "LEASE_S": 30,
        "LIFETIME_S": 900,
        "READINESS_S": 10,
        "MEMORY_LIMIT_BYTES": 536870912,
    }
    assert all(f"`OVERSIGHT_PREVIEW_{name}`" in docs for name in defaults)
    assert all(f"`{reason}`" in docs for reason in get_args(PreviewReason))
    profile = resolve_preview_profile({}, 8710, None)
    assert (profile.adapter, profile.interpreter, profile.ports, profile.origins) == (
        None,
        None,
        (),
        (),
    )
    assert (
        profile.heartbeat_s,
        profile.lease_s,
        profile.lifetime_s,
        profile.readiness_s,
        profile.memory_limit_bytes,
    ) == (10, 30, 900, 10, 536870912)
    assert not profile.production_open
    assert resolve_preview_profile(
        {"OVERSIGHT_PREVIEW_ORIGINS": " http://127.0.0.1:8710"}, 8710, None
    ).origins == (" http://127.0.0.1:8710",)


@pytest.mark.parametrize(
    "key", ["PORTS", "HEARTBEAT_S", "LEASE_S", "LIFETIME_S", "READINESS_S", "MEMORY_LIMIT_BYTES"]
)
def test_malformed_numeric_configuration_raises_without_an_enable_flag(key):
    with pytest.raises(ValueError):
        resolve_preview_profile({f"OVERSIGHT_PREVIEW_{key}": "invalid"}, 8710, None)


def test_replay_policy_is_head_prepend_and_never_uses_embedding_origins():
    config = (Path(__file__).resolve().parents[3] / "frontend/vite.config.ts").read_text()
    assert 'injectTo: "head-prepend"' in config and "content: \"frame-src 'none'\"" in config
    assert "OVERSIGHT_PREVIEW_ORIGINS" not in config
    assert "OVERSIGHT_PREVIEW_PORTS" in config


@pytest.mark.anyio
@pytest.mark.parametrize("pool", [(8710,), (5710,), (19681, 8710)])
async def test_disabled_invalid_pool_never_permits_framing_backend_or_vite(tmp_path, pool):
    from oversight.workspace import PreviewProfile

    (tmp_path / "index.html").write_text("<html><head></head></html>")
    app = FastAPI()
    app.state.settings = Settings(
        port=8710, vite_port=5710, preview_profile=PreviewProfile(ports=pool)
    )
    assert add_static(app, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8710"
    ) as client:
        assert (await client.get("/")).headers["content-security-policy"] == "frame-src 'none'"
