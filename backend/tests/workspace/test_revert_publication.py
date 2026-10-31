"""Inverse publication, checkout settlement and repeated application."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from backend.tests.workspace.workspace_helpers import api as api
from backend.tests.workspace.workspace_helpers import (
    effect_args,
    facts,
    prepared,
)
from backend.tests.workspace.workspace_helpers import pytestmark as pytestmark

from oversight.workspace import ProcessResult


async def test_apply_landed_preserves_later_work(workspace, api):
    item = await prepared(workspace, api)
    async with workspace.locks(main=True, agent_id="agent-1"):
        parent_tree = await workspace.git_ok(workspace.main, "rev-parse", "HEAD^^{tree}")
        assert parent_tree.strip() != item.inverse_tree
        outcome = await api.apply_inverse(workspace, **effect_args(item))
        assert outcome.kind == "landed" and outcome.main_commit == item.inverse_commit
        assert (workspace.main.root / "later.py").read_text() == "later = 1\n"
        assert (workspace.main.root / "shop/mod.py").read_text().endswith("return 1\n")
        assert (await facts(workspace))[2:] == (item.inverse_tree + "\n", "", "")
        assert await api.classify_inverse(workspace, **effect_args(item)) == "landed"


async def test_competing_head_untouched(workspace, api):
    item = await prepared(workspace, api)
    async with workspace.locks(main=True, agent_id="agent-1"):
        await workspace.git_ok(workspace.main, "commit", "--allow-empty", "-qm", "Competitor")
        before = await facts(workspace)
        outcome = await api.apply_inverse(workspace, **effect_args(item))
        assert outcome.kind == "main_moved" and outcome.main_commit == before[1].strip()
        assert await facts(workspace) == before
        assert await api.classify_inverse(workspace, **effect_args(item)) == "unknown"


@pytest.mark.parametrize("dirty", ["index", "checkout", "untracked", "detached"])
async def test_dirty_refused_and_detached_unknown_before_cas(workspace, api, dirty, monkeypatch):
    item = await prepared(workspace, api)
    async with workspace.locks(main=True, agent_id="agent-1"):
        path = workspace.main.root / ("new.py" if dirty == "untracked" else "later.py")
        if dirty == "detached":
            await workspace.git_ok(workspace.main, "checkout", "--detach", item.main_commit)
        else:
            path.write_text("changed = 2\n")
            if dirty == "index":
                await workspace.git_ok(workspace.main, "add", "later.py")
        before = await workspace.git_ok(workspace.main, "rev-parse", "refs/heads/main")
        real, calls = workspace.git, []

        async def observe(tree, *args, **kwargs):
            calls.append(args[0])
            return await real(tree, *args, **kwargs)

        monkeypatch.setattr(workspace, "git", observe)
        if dirty == "detached":
            assert (await api.apply_inverse(workspace, **effect_args(item))).kind == "unknown"
        else:
            with pytest.raises(api.InverseError):
                await api.apply_inverse(workspace, **effect_args(item))
        assert "update-ref" not in calls
        assert await workspace.git_ok(workspace.main, "rev-parse", "refs/heads/main") == before


@pytest.mark.parametrize("stage", ["before", "after"])
@pytest.mark.parametrize("failure", ["timeout", "cancel", "error"])
async def test_uncertain_cas_classified_once(workspace, api, monkeypatch, stage, failure):
    item = await prepared(workspace, api)
    real, calls = workspace.git, []

    async def inject(tree, *args, **kwargs):
        if args[0] == "update-ref":
            calls.append(args)
            if stage == "after":
                await real(tree, *args, **kwargs)
            if failure == "cancel":
                raise asyncio.CancelledError
            if failure == "error":
                raise OSError("unconfirmed")
            return ProcessResult(-15, "", "timeout", True, False, 1)
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", inject)
    async with workspace.locks(main=True, agent_id="agent-1"):
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await api.apply_inverse(workspace, **effect_args(item))
        else:
            result = await api.apply_inverse(workspace, **effect_args(item))
            assert result.kind == ("landed" if stage == "after" else "unknown")
        assert len(calls) == 1
        assert await api.classify_inverse(workspace, **effect_args(item)) == (
            "landed" if stage == "after" else "untouched"
        )


@pytest.mark.parametrize("blocked", [False, True])
async def test_checkout_after_cas_finishes_or_unknown(workspace, api, monkeypatch, blocked):
    item = await prepared(workspace, api)
    real, attempts = workspace.git, []

    async def inject(tree, *args, **kwargs):
        if args[0] == "read-tree":
            attempts.append(args)
            if blocked or len(attempts) == 1:
                raise OSError("checkout interrupted")
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", inject)
    async with workspace.locks(main=True, agent_id="agent-1"):
        result = await api.apply_inverse(workspace, **effect_args(item))
        assert result.kind == ("unknown" if blocked else "landed")
        assert 2 <= len(attempts) <= 4
        assert (
            await workspace.git_ok(workspace.main, "rev-parse", "HEAD")
            == item.inverse_commit + "\n"
        )


async def test_unreadable_evidence_unknown(workspace, api, monkeypatch):
    item = await prepared(workspace, api)
    monkeypatch.setattr(workspace, "git", AsyncMock(side_effect=OSError("unreadable")))
    async with workspace.locks(main=True, agent_id="agent-1"):
        assert await api.classify_inverse(workspace, **effect_args(item)) == "unknown"


@pytest.mark.parametrize("operation", ["apply_inverse", "classify_inverse"])
@pytest.mark.parametrize("held", ["none", "main", "agent", "wrong_agent"])
async def test_effect_and_settlement_require_original_locks(workspace, api, operation, held):
    item = await prepared(workspace, api)

    async def attempt():
        with pytest.raises(api.InverseError, match="lock"):
            await getattr(api, operation)(workspace, **effect_args(item))

    if held == "none":
        await attempt()
    else:
        async with workspace.locks(
            main=held != "agent",
            agent_id={"main": None, "agent": "agent-1", "wrong_agent": "agent-2"}[held],
        ):
            await attempt()


@pytest.mark.parametrize("blocked", [False, True])
async def test_apply_already_published_with_old_checkout(workspace, api, monkeypatch, blocked):
    item = await prepared(workspace, api)
    real = workspace.git
    calls = []
    async with workspace.locks(main=True, agent_id="agent-1"):
        await workspace.git_ok(
            workspace.main, "update-ref", "refs/heads/main", item.inverse_commit, item.main_commit
        )

        async def observe(tree, *args, **kwargs):
            calls.append(args[0])
            if blocked and args[0] == "read-tree":
                raise OSError("checkout still unavailable")
            return await real(tree, *args, **kwargs)

        monkeypatch.setattr(workspace, "git", observe)
        outcome = await api.apply_inverse(workspace, **effect_args(item))
        assert outcome.kind == ("unknown" if blocked else "landed")
        assert "update-ref" not in calls


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "unreaped", "truncated", "inconsistent", "io"])
async def test_preflight_read_failure_settles_published_inverse(
    workspace, api, monkeypatch, failure, persistent
):
    item = await prepared(workspace, api)
    real = workspace.git
    injected = False
    effects = []

    async def unreadable(tree, *args, **kwargs):
        nonlocal injected
        if args[0] == "update-ref":
            effects.append(args)
        target = ("rev-parse", "HEAD") if failure == "inconsistent" else ("symbolic-ref", "HEAD")
        result = await real(tree, *args, **kwargs)
        if args == target and (persistent or not injected):
            injected = True
            if failure == "io":
                raise OSError("unreadable preflight")
            return replace(
                result,
                returncode=None if failure == "unreaped" else result.returncode,
                timed_out=failure == "timeout",
                truncated=failure == "truncated",
                stdout=item.main_commit + "\n" if failure == "inconsistent" else result.stdout,
            )
        return result

    async with workspace.locks(main=True, agent_id="agent-1"):
        await workspace.git_ok(
            workspace.main, "update-ref", "refs/heads/main", item.inverse_commit, item.main_commit
        )
        monkeypatch.setattr(workspace, "git", unreadable)
        outcome = await api.apply_inverse(workspace, **effect_args(item))
        assert outcome.kind == ("unknown" if persistent else "landed")
        assert injected and not effects


@pytest.mark.parametrize("dirty", ["index", "checkout"])
async def test_preflight_dirty_refusal_rechecks_late_inverse(workspace, api, monkeypatch, dirty):
    item = await prepared(workspace, api)
    real = workspace.git
    injected = False
    effects = []

    async def late_publication(tree, *args, **kwargs):
        nonlocal injected
        if args[0] == "update-ref":
            effects.append(args)
        if not injected and args[0] == ("write-tree" if dirty == "index" else "diff-files"):
            injected = True
            await real(tree, "update-ref", "refs/heads/main", item.inverse_commit, item.main_commit)
            if dirty == "index":
                await real(tree, "read-tree", "-m", "-u", item.main_commit, item.inverse_commit)
            else:
                (tree.root / "shop/mod.py").write_text("foreign edit\n")
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", late_publication)
    async with workspace.locks(main=True, agent_id="agent-1"):
        outcome = await api.apply_inverse(workspace, **effect_args(item))
        assert outcome.kind == ("landed" if dirty == "index" else "unknown")
        assert injected and not effects
        if dirty == "checkout":
            assert (workspace.main.root / "shop/mod.py").read_text() == "foreign edit\n"


async def test_cas_mismatch_naming_inverse_settles_checkout(workspace, api, monkeypatch):
    item = await prepared(workspace, api)
    real = workspace.git
    calls = []

    # The first real update-ref lands C. apply_inverse's own update-ref, the returned
    # second call, fails with main already at C: that is the mismatch under test.
    # Collapsing the two calls into one would turn this into a happy-path test.
    async def race(tree, *args, **kwargs):
        if args[0] == "update-ref":
            calls.append(args)
            await real(tree, *args, **kwargs)
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", race)
    async with workspace.locks(main=True, agent_id="agent-1"):
        outcome = await api.apply_inverse(workspace, **effect_args(item))
        assert outcome.kind == "landed" and outcome.main_commit == item.inverse_commit
        assert len(calls) == 1
        assert (await facts(workspace))[2:] == (item.inverse_tree + "\n", "", "")


@pytest.mark.parametrize("dirty", [False, True])
async def test_cancelled_cas_late_publication_requires_verified_checkout(
    workspace, api, monkeypatch, dirty
):
    item = await prepared(workspace, api)
    real = workspace.git
    pending = False
    observed = []
    classify = api.classify_inverse

    async def late_cas(tree, *args, **kwargs):
        nonlocal pending
        if args[0] == "update-ref":
            pending = True
            raise asyncio.CancelledError
        if pending and args[0] == "write-tree":
            pending = False
            await real(tree, "update-ref", "refs/heads/main", item.inverse_commit, item.main_commit)
            if dirty:
                (tree.root / "shop/mod.py").write_text("foreign edit\n")
        return await real(tree, *args, **kwargs)

    async def observe(*args, **kwargs):
        result = await classify(*args, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(workspace, "git", late_cas)
    monkeypatch.setattr(api, "classify_inverse", observe)
    async with workspace.locks(main=True, agent_id="agent-1"):
        with pytest.raises(asyncio.CancelledError):
            await api.apply_inverse(workspace, **effect_args(item))
        assert observed == ["unknown"]
        assert await classify(workspace, **effect_args(item)) == ("unknown" if dirty else "landed")
        if dirty:
            assert (workspace.main.root / "shop/mod.py").read_text() == "foreign edit\n"


async def test_cas_comparison_race_returns_verified_competitor(workspace, api, monkeypatch):
    item = await prepared(workspace, api)
    real = workspace.git
    competitor = ""

    async def race(tree, *args, **kwargs):
        nonlocal competitor
        if args[0] == "update-ref":
            await real(tree, "commit", "--allow-empty", "-qm", "Concurrent commit")
            competitor = (await real(tree, "rev-parse", "HEAD")).stdout.strip()
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", race)
    async with workspace.locks(main=True, agent_id="agent-1"):
        outcome = await api.apply_inverse(workspace, **effect_args(item))
        assert outcome.kind == "main_moved" and outcome.main_commit == competitor
        assert (await workspace.git_ok(workspace.main, "rev-parse", "HEAD")).strip() == competitor
        assert (workspace.main.root / "shop/mod.py").read_text().endswith("return 2\n")


@pytest.mark.parametrize(
    "field",
    range(7),
    ids=[
        "tree",
        "parents",
        "author_name",
        "author_email",
        "committer_name",
        "committer_email",
        "message",
    ],
)
async def test_each_commit_field_verified_before_publication(workspace, api, monkeypatch, field):
    item = await prepared(workspace, api)
    real, effects = workspace.git, []

    async def corrupt(tree, *args, **kwargs):
        result = await real(tree, *args, **kwargs)
        if args[0] == "show":
            fields = result.stdout.split("\0", 6)
            fields[field] = "unexpected"
            return replace(result, stdout="\0".join(fields))
        if args[0] == "update-ref":
            effects.append(args)
        return result

    monkeypatch.setattr(workspace, "git", corrupt)
    async with workspace.locks(main=True, agent_id="agent-1"):
        with pytest.raises(api.InverseError, match="differ"):
            await api.apply_inverse(workspace, **effect_args(item))
        assert not effects
        assert await api.classify_inverse(workspace, **effect_args(item)) == "unknown"


@pytest.mark.parametrize("hidden", ["--assume-unchanged", "--skip-worktree"])
async def test_hidden_checkout_changes_refused(workspace, api, hidden):
    item = await prepared(workspace, api)
    async with workspace.locks(main=True, agent_id="agent-1"):
        await workspace.git_ok(workspace.main, "update-index", hidden, "later.py")
        (workspace.main.root / "later.py").write_text("hidden = 3\n")
        with pytest.raises(api.InverseError, match="hidden"):
            await api.apply_inverse(workspace, **effect_args(item))
        assert await api.classify_inverse(workspace, **effect_args(item)) == "unknown"


async def test_partial_dirty_checkout_is_unknown_and_preserved(workspace, api):
    item = await prepared(workspace, api)
    async with workspace.locks(main=True, agent_id="agent-1"):
        await workspace.git_ok(
            workspace.main, "update-ref", "refs/heads/main", item.inverse_commit, item.main_commit
        )
        path = workspace.main.root / "shop/mod.py"
        path.write_text("partially updated or foreign\n")
        before = await facts(workspace)
        assert await api.classify_inverse(workspace, **effect_args(item)) == "unknown"
        assert await facts(workspace) == before


async def test_reapply_landed_candidate_does_not_publish_twice(workspace, api, monkeypatch):
    item = await prepared(workspace, api)
    async with workspace.locks(main=True, agent_id="agent-1"):
        assert (await api.apply_inverse(workspace, **effect_args(item))).kind == "landed"
        real, effects = workspace.git, []

        async def observe(tree, *args, **kwargs):
            if args[0] == "update-ref":
                effects.append(args)
            return await real(tree, *args, **kwargs)

        monkeypatch.setattr(workspace, "git", observe)
        result = await api.apply_inverse(workspace, **effect_args(item))
        assert result.kind == "landed" and not effects


async def test_repeated_cancellation_settles_in_owner_task(workspace, api, monkeypatch):
    item = await prepared(workspace, api)
    real, owner = workspace.git, asyncio.current_task()
    calls = 0

    async def interrupt(tree, *args, **kwargs):
        nonlocal calls
        assert asyncio.current_task() is owner
        if args[0] == "update-ref":
            await real(tree, *args, **kwargs)
            raise asyncio.CancelledError
        if args[0] == "read-tree":
            calls += 1
            if calls == 1:
                raise asyncio.CancelledError
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", interrupt)
    async with workspace.locks(main=True, agent_id="agent-1"):
        with pytest.raises(asyncio.CancelledError):
            await api.apply_inverse(workspace, **effect_args(item))
        assert calls == 2
        assert (await facts(workspace))[2:4] == (item.inverse_tree + "\n", "")


@pytest.mark.parametrize("field", ["main_commit", "inverse_commit", "inverse_tree"])
async def test_foreign_candidate_tuple_refused(workspace, api, field):
    item = await prepared(workspace, api)
    args = effect_args(item) | {field: "1" * 40}
    async with workspace.locks(main=True, agent_id="agent-1"):
        with pytest.raises(api.InverseError, match="not prepared"):
            await api.apply_inverse(workspace, **args)
