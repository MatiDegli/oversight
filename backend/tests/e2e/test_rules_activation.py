"""Activation uses real HTTP selection and ordinary fake author/reviewer receipts."""

import asyncio
from dataclasses import replace

import pytest
from live_helpers import live_server

from oversight.changes import _mode

# Root conftest adds tools/ to sys.path for the shared standalone harness.
from rules_scenario import Events, merge, needs_you, revert, select, settings  # pyright: ignore[reportMissingImports]

pytestmark = pytest.mark.anyio


async def test_rules_merge(tmp_path):
    async with (
        live_server(settings(tmp_path / ".runtime")) as server,
        Events(server.client) as events,
    ):
        async with asyncio.timeout(60):
            await select(server.client, events)
            attempt = asyncio.create_task(merge(server.client, events))
            try:
                reviewed = (await server.until(lambda e: e.type == "change.reviewed"))[-1]
                await asyncio.gather(*list(server.live._reviews.values()))
                manager = server.live._manager
                assert manager is not None
                assert reviewed.seq in manager._ctx.assessment.events
                await attempt
            finally:
                attempt.cancel()
                await asyncio.gather(attempt, return_exceptions=True)


async def test_rules_merge_revert(tmp_path):
    async with (
        live_server(settings(tmp_path / ".runtime")) as server,
        Events(server.client) as events,
    ):
        async with asyncio.timeout(60):
            await select(server.client, events)
            original = (await server.client.get("/files?path=shop/pricing.py")).json()["content"]
            resolution = await merge(server.client, events)
            outcome = await revert(server.client, events, resolution)
            assert (await server.client.get("/files?path=shop/pricing.py")).json()[
                "content"
            ] == original
            workspace = server.live.workspace
            async with workspace.locks(main=True):
                parents = await workspace.git(
                    workspace.main, "show", "-s", "--format=%P", outcome.payload.revert_commit
                )
            assert (
                parents.returncode == 0
                and parents.stdout.strip() == resolution.payload.merge_commit
            )


async def test_rules_needs_you(tmp_path):
    async with (
        live_server(settings(tmp_path / ".runtime")) as server,
        Events(server.client) as events,
    ):
        async with asyncio.timeout(60):
            await select(server.client, events)
            await needs_you(server.client, events, tmp_path / "rules-reviewer.json")


async def test_restart_returns_to_ask_me(tmp_path):
    config = settings(tmp_path / ".runtime")
    async with live_server(config) as server, Events(server.client) as events:
        await select(server.client, events)
    async with live_server(config) as restarted:
        assert (await restarted.client.get("/merge-mode")).json() == {
            "mode": "ask_me",
            "revision": 0,
        }
        assert restarted.events()[0].payload.merge_mode.mode == "ask_me"


@pytest.mark.parametrize(
    "condition,reason",
    [
        ("unavailable", "Network isolation C"),
        ("revoked", "Network isolation C"),
        ("suspended", "Rules authority is suspended"),
        ("suspect", "Main's integrity is uncertain"),
        ("cross_review", "Cross-review is off"),
        ("missing_reviewer", "reviewer model is not configured"),
        ("same_reviewer", "reviewer model must differ"),
        ("spend", "known_spend"),
        ("protected", "protected_paths"),
    ],
)
async def test_other_conditions_refuse_with_latch_open(tmp_path, monkeypatch, condition, reason):
    config = settings(tmp_path / ".runtime")
    if condition == "cross_review":
        config = replace(config, cross_review=False)
    elif condition == "same_reviewer":
        config = replace(config, review_model="fake")
    elif condition == "spend":
        config.runtime_dir.mkdir()
        (config.runtime_dir / "ledger.json").write_text('{"spent_usd":0.75,"unknown":[]}')
    async with live_server(config) as server, Events(server.client) as events:
        isolation = server.live.workspace._isolation
        integrity = server.live.workspace.integrity
        if condition == "unavailable":
            isolation._born = False
        elif condition == "revoked":
            isolation.invalidate("Scripted revocation after startup")
        elif condition == "suspended":
            integrity.rules_suspended = True
        elif condition == "suspect":
            integrity.main_suspect = True
        if condition == "missing_reviewer":

            def missing(name):
                raise ValueError("Scripted unavailable reviewer profile")

            monkeypatch.setattr(_mode, "resolve_profile", missing)
        before = (await server.client.get("/graph")).json()["graph"]["commit"]
        if condition not in {"spend", "protected"}:
            response = await server.client.put(
                "/merge-mode", json={"mode": "merge_when_safe", "revision": 0}
            )
            assert response.status_code == 409 and reason in response.json()["error"]["message"]
            assert (await server.client.get("/merge-mode")).json() == {
                "mode": "ask_me",
                "revision": 0,
            }
            assert not any(e.type == "merge_mode.changed" for e in server.events())
        else:
            await select(server.client, events)
            response = await server.client.post(
                "/agents",
                json={
                    "task": "Document the file.",
                    "slot": "B" if condition == "protected" else "A",
                },
            )
            assert response.status_code == 201
            proposal = await events.wait("change.proposed")
            assessment = await events.match(
                lambda e: (
                    e.type == "change.assessed"
                    and any(r.rule == reason and r.result == "fail" for r in e.payload.rules)
                )
            )
            assert assessment.payload.head_commit == proposal.payload.head_commit
            await events.wait("change.reviewed")
            await asyncio.gather(*list(server.live._reviews.values()))
            assert not any(e.type == "change.resolved" for e in server.events())
        assert (await server.client.get("/graph")).json()["graph"]["commit"] == before
