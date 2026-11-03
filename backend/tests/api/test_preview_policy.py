"""Exact application-port framing and privacy before routing; no Origin or proxy widening."""

import json
import os
from html.parser import HTMLParser
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from oversight.api.errors import install_error_handlers
from oversight.api.preview_policy import frame_policy
from oversight.api.static import add_static
from oversight.settings import Settings, resolve_preview_profile
from oversight.workspace import run_process

ROOT = Path(__file__).resolve().parents[3]


class HeadChildren(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_head = False
        self.children = []

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        elif self.in_head:
            self.children.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


async def vite_script(script, *args):
    result = await run_process(
        ["pnpm", "exec", "node", "--input-type=module", "-e", script, *map(str, args)],
        cwd=ROOT / "frontend",
        timeout_s=90,
        env={
            "PATH": os.environ["PATH"],
            "OVERSIGHT_PORT": "8710",
            "OVERSIGHT_VITE_PORT": "5710",
            "OVERSIGHT_PREVIEW_PORTS": "19681,19682",
            "OVERSIGHT_PREVIEW_ORIGINS": "http://127.0.0.1:8710",
        },
    )
    assert result.returncode == 0 and not result.timed_out, result.stdout + result.stderr


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
    from oversight.api.security import ApiGuard, AuthorityPolicy

    app.add_middleware(ApiGuard, policy=AuthorityPolicy(8710))
    assert add_static(app, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8710"
    ) as client:
        for path in ("/", "/nested/spa", "/index.html"):
            response = await client.get(path)
            assert (
                response.headers["content-security-policy"]
                == "frame-src http://localhost:19681 http://localhost:19682"
            )
            assert response.headers["cache-control"] == "no-cache"
            assert "8710" not in response.headers["content-security-policy"]
        assert "content-security-policy" not in (await client.get("/asset.js")).headers


def test_documented_configuration_and_reason_inventory_are_exact():
    from typing import get_args

    from oversight_contracts.previews import PreviewReason

    docs = (Path(__file__).resolve().parents[3] / "docs/isolation.md").read_text()
    defaults = {
        "ENABLED": False,
        "ADAPTER": None,
        "PYTHON": None,
        "PORTS": (),
        "ORIGINS": (),
        "HEARTBEAT_S": 10,
        "LEASE_S": 600,
        "LIFETIME_S": 900,
        "READINESS_S": 30,
        "MEMORY_LIMIT_BYTES": 536870912,
    }
    assert all(f"`OVERSIGHT_PREVIEW_{name}`" in docs for name in defaults)
    assert all(f"`{reason}`" in docs for reason in get_args(PreviewReason))
    profile = resolve_preview_profile({}, 8710, None)
    assert profile.enabled is defaults["ENABLED"]
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
    ) == (10, 600, 900, 30, 536870912)
    assert not profile.production_open
    assert resolve_preview_profile(
        {"OVERSIGHT_PREVIEW_ORIGINS": " http://127.0.0.1:8710"}, 8710, None
    ).origins == (" http://127.0.0.1:8710",)


def test_documented_preview_opening_requires_explicit_opt_in_and_retains_limits():
    docs = (ROOT / "docs/isolation.md").read_text()
    policy = " ".join(docs.split("### Dormant person-owned preview supervision", 1)[1].split())
    assert "`PreviewProfile.production_open` returns the frozen `enabled` flag" in policy
    assert (
        "`POST /previews` only when every trusted-demo, configuration and isolation C check"
        in policy
    )
    assert "No inferred enablement or unisolated fallback exists" in policy
    assert "exact absolute `backend/src/oversight/preview_adapter.py`" in policy
    assert "filesystem" in policy and "not isolated" in policy
    row = next(
        line for line in docs.splitlines() if line.startswith("| `OVERSIGHT_PREVIEW_ENABLED`")
    )
    assert "| `false` |" in row
    assert "literal `true` or `false`" in row and "raise" in row
    assert "There is no production opening switch" not in policy
    assert "No enable flag" not in policy
    assert "Production opening remains disabled" not in policy


def test_preview_example_and_decision_document_the_default_closed_switch():
    example = (ROOT / ".env.example").read_text()
    assignments = [
        line for line in example.splitlines() if line.startswith("OVERSIGHT_PREVIEW_ENABLED=")
    ]
    assert assignments == ["OVERSIGHT_PREVIEW_ENABLED=false"]
    assert not resolve_preview_profile(
        dict(line.split("=", 1) for line in assignments), 8710, None
    ).enabled
    comments = " ".join(example.replace("# ", "").split())
    assert "literal true or false" in comments and "default false" in comments
    assert "trusted demo" in comments and "isolation C" in comments
    assert "absolute adapter" in comments and "not isolated" in comments
    decisions = (
        (ROOT / "DECISIONS.md")
        .read_text()
        .split(
            "## 2026-11-03 — Dormant preview uses the shared Git lease and durable ownership", 1
        )[1]
        .split("\n## ", 1)[0]
    )
    assert "OVERSIGHT_PREVIEW_ENABLED" in decisions and "default false" in decisions


def test_capture_inventory_distinguishes_uninspected_matrix_from_missing_windows_proof():
    docs = (ROOT / "docs/isolation.md").read_text()
    audit = docs.split("### Hardening and technical audit recheck", 1)[1]
    assert "Windows browser proof remains missing" in audit
    assert "C's earlier capture matrix was not inspected in this round" in audit
    assert "Trust remains withheld" in audit


@pytest.mark.parametrize(
    "key", ["PORTS", "HEARTBEAT_S", "LEASE_S", "LIFETIME_S", "READINESS_S", "MEMORY_LIMIT_BYTES"]
)
def test_malformed_numeric_configuration_raises_without_an_enable_flag(key):
    with pytest.raises(ValueError):
        resolve_preview_profile({f"OVERSIGHT_PREVIEW_{key}": "invalid"}, 8710, None)


@pytest.mark.anyio
async def test_built_replay_starts_head_with_frame_refusal_even_with_configured_pool(tmp_path):
    await vite_script(
        """
        import { build } from 'vite';
        await build({mode: 'replay', build: {outDir: process.argv[1]}});
        """,
        tmp_path,
    )
    document = HeadChildren()
    document.feed((tmp_path / "index.html").read_text())
    assert document.children[0] == (
        "meta",
        {"http-equiv": "Content-Security-Policy", "content": "frame-src 'none'"},
    )


@pytest.mark.anyio
async def test_actual_vite_html_responses_carry_exact_pool_header(tmp_path):
    evidence = tmp_path / "headers.json"
    await vite_script(
        """
        import { createServer } from 'vite';
        import { writeFile } from 'node:fs/promises';
        const server = await createServer({server: {port: 0, strictPort: false}});
        try {
          await server.listen();
          const origin = server.resolvedUrls.local[0];
          const headers = [];
          for (const path of ['', 'nested/spa']) {
            const response = await fetch(origin + path, {signal: AbortSignal.timeout(5000)});
            if (response.status !== 200) throw new Error('Vite HTML unavailable');
            headers.push(response.headers.get('content-security-policy'));
            await response.text();
          }
          await writeFile(process.argv[1], JSON.stringify(headers));
        } finally { await server.close(); }
        """,
        evidence,
    )
    assert (
        json.loads(evidence.read_text())
        == ["frame-src http://127.0.0.1:19681 http://127.0.0.1:19682"] * 2
    )


@pytest.mark.anyio
async def test_general_browser_project_keeps_real_c_suite_in_explicit_project():
    for project in ("browser", "preview-acceptance"):
        result = await run_process(
            ["pnpm", "exec", "vitest", "list", "--project", project, "--filesOnly"],
            cwd=ROOT / "frontend",
            timeout_s=30,
            env={"PATH": os.environ["PATH"]},
        )
        assert result.returncode == 0 and not result.timed_out, result.stdout + result.stderr
        acceptance = "src/app/preview/PreviewAcceptance.browser.test.ts"
        files = result.stdout.splitlines()
        assert any(acceptance in path for path in files) == (project == "preview-acceptance")
        if project == "preview-acceptance":
            assert len([path for path in files if path.endswith(".test.ts")]) == 1


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


@pytest.mark.parametrize("parent,frame", [("localhost", "127.0.0.1"), ("127.0.0.1", "localhost")])
def test_reviewed_opposite_loopback_authorities(parent, frame):
    from oversight.api.preview_policy import request_frame_policy

    policy = frame_policy((19681, 19682), reserved=(8710, 5710))
    scope = {"headers": [(b"host", f"{parent}:8710".encode())]}
    result = request_frame_policy(policy, scope)
    assert result == f"frame-src http://{frame}:19681 http://{frame}:19682"
    assert f"http://{parent}:" not in result and "8710" not in result
    for host in ("evil.test:8710", "[::1]:8710", "localhost.evil:8710"):
        assert (
            request_frame_policy(policy, {"headers": [(b"host", host.encode())]})
            == "frame-src 'none'"
        )
