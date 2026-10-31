"""Inverse transactions on disposable Git repositories; no provider or credits."""

import asyncio
import sys
import textwrap
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oversight.workspace import ProcessResult, SuiteRun, run_process
from oversight.workspace.suite import SuiteInterrupted
from oversight_contracts.events import SuiteResult

pytestmark = pytest.mark.anyio
MESSAGE = "Revert rule merge\n\nPreserve later work.\n"


@pytest.fixture
def api():
    from oversight.workspace import revert

    return revert


async def candidate(workspace, api):
    """H contains later work; T removes only the selected change."""
    async with workspace.locks(main=True):
        main = workspace.main
        h0 = (await workspace.git_ok(main, "rev-parse", "HEAD")).strip()
        path = main.root / "shop/mod.py"
        original = path.read_text()
        path.write_text("def value():\n    return 2\n")
        await workspace.git_ok(main, "add", "shop/mod.py")
        await workspace.git_ok(main, "commit", "-qm", "Rule merge")
        path = main.root / "later.py"
        path.write_text("later = 1\n")
        await workspace.git_ok(main, "add", "later.py")
        await workspace.git_ok(main, "commit", "-qm", "Later work")
        head = (await workspace.git_ok(main, "rev-parse", "HEAD")).strip()
        path = main.root / "shop/mod.py"
        path.write_text(original)
        await workspace.git_ok(main, "add", "shop/mod.py")
        target = (await workspace.git_ok(main, "write-tree")).strip()
        await workspace.git_ok(main, "read-tree", "-m", "-u", head)
        assert head != h0
    return head, target


async def facts(workspace):
    git = workspace.git_ok
    main = workspace.main
    return tuple(
        [
            await git(main, *args)
            for args in (
                ("symbolic-ref", "HEAD"),
                ("rev-parse", "HEAD"),
                ("write-tree",),
                ("status", "--porcelain", "-z"),
                ("diff", "HEAD"),
            )
        ]
    )


async def test_prepare_exact_unreachable_commit_and_verified_cleanup(workspace, api):
    h, t = await candidate(workspace, api)
    async with workspace.locks(main=True, agent_id="agent-1"):
        before = await facts(workspace)
        async with api.prepare_inverse(
            workspace,
            agent_id="agent-1",
            main_commit=h,
            inverse_tree=t,
            message=MESSAGE,
            timeout_s=5,
        ) as item:
            assert (item.main_commit, item.inverse_tree) == (h, t)
            assert item.tree.agent_id == "agent-1"
            git = workspace.git_ok
            assert (await git(item.tree, "rev-parse", "HEAD")).strip() == item.inverse_commit
            assert (await git(item.tree, "rev-parse", "HEAD^{tree}")).strip() == t
            assert (await git(item.tree, "rev-list", "--parents", "-n", "1", "HEAD")).split() == [
                item.inverse_commit,
                h,
            ]
            identity = await git(item.tree, "show", "-s", "--format=%an|%ae|%cn|%ce", "HEAD")
            assert identity.strip() == "User|user@oversight.invalid|User|user@oversight.invalid"
            assert not await git(workspace.main, "for-each-ref", "--contains", item.inverse_commit)
            assert not item.cleaned
            assert await facts(workspace) == before
            root, admin = item.tree.root, item.tree.git_identity.directory
        assert item.cleaned
        assert not root.exists() and not admin.exists()
        assert str(root) not in await git(workspace.main, "worktree", "list", "--porcelain")
        assert await facts(workspace) == before


async def test_empty_inverse_refused_before_any_object(workspace, api, monkeypatch):
    async with workspace.locks(main=True, agent_id="agent-1"):
        h = await workspace.main.head()
        t = (await workspace.git_ok(workspace.main, "rev-parse", "HEAD^{tree}")).strip()
        real = workspace.git
        calls = []

        async def observe(tree, *args, **kwargs):
            calls.append(args[0])
            return await real(tree, *args, **kwargs)

        monkeypatch.setattr(workspace, "git", observe)
        with pytest.raises(api.InverseError, match="empty"):
            async with api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ):
                pytest.fail("empty inverse accepted")
        assert "commit-tree" not in calls and "worktree" not in calls
        assert not list(workspace.tmp_dir.glob("revert-*"))


@pytest.mark.parametrize("held", ["none", "main", "agent", "wrong_agent"])
async def test_prepare_requires_both_original_locks(workspace, api, held):
    h, t = await candidate(workspace, api)

    async def attempt():
        with pytest.raises(api.InverseError, match="lock"):
            async with api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ):
                pytest.fail("missing caller locks accepted")

    if held == "none":
        await attempt()
    else:
        async with workspace.locks(
            main=held != "agent",
            agent_id={"main": None, "agent": "agent-1", "wrong_agent": "agent-2"}[held],
        ):
            await attempt()


@pytest.mark.parametrize("main_held", [False, True])
async def test_invalid_agent_does_not_create_lock(workspace, api, main_held):
    async def attempt():
        before = dict(workspace.operations._agents)
        try:
            with pytest.raises(api.InverseError, match="invalid original agent id"):
                async with api.prepare_inverse(
                    workspace,
                    agent_id="../invalid",
                    main_commit="1" * 40,
                    inverse_tree="2" * 40,
                    message=MESSAGE,
                    timeout_s=5,
                ):
                    pytest.fail("invalid agent accepted")
        finally:
            assert workspace.operations._agents == before

    if main_held:
        async with workspace.locks(main=True):
            await attempt()
    else:
        await attempt()


async def test_reentrant_required_suite(workspace, api, monkeypatch):
    h, t = await candidate(workspace, api)
    suite = AsyncMock(
        return_value=SuiteRun(
            SuiteResult(
                outcome="passed", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]
            ),
            "",
            problems=(),
        )
    )
    monkeypatch.setattr("oversight.workspace.suite_guard.run_suite", suite)
    await workspace.check_isolation()
    async with workspace.locks(main=True, agent_id="agent-1"):
        async with api.prepare_inverse(
            workspace,
            agent_id="agent-1",
            main_commit=h,
            inverse_tree=t,
            message=MESSAGE,
            timeout_s=5,
        ) as item:
            run = await workspace.run_tests(item.tree, timeout_s=5, isolation="required")
            assert run.tested_commit == item.inverse_commit
        assert api.inverse_suite_problem(run, tree=t, cleaned=item.cleaned) is None


async def suite_signal(task, gate, timeout_s: float = 60):
    signal = asyncio.create_task(gate.wait_signal())
    try:
        done, _ = await asyncio.wait(
            {signal, task}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task  # Surface setup/isolation failures instead of waiting for an absent child.
            pytest.fail("suite owner ended before signaling")
        if signal not in done:
            pytest.fail("suite child did not signal within its deadline")
        return int(await signal)
    finally:
        signal.cancel()
        # Gate releases and joins its FIFO worker before cancellation escapes.
        await asyncio.gather(signal, return_exceptions=True)


@pytest.mark.parametrize("owner_fails", [False, True])
async def test_suite_signal_watches_owner_and_deadline(gate_factory, owner_fails):
    gate = gate_factory("absent-suite")
    error = OSError("suite setup failed")

    async def owner():
        if owner_fails:
            raise error
        await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    try:
        async with asyncio.timeout(5):
            with pytest.raises(OSError if owner_fails else pytest.fail.Exception) as caught:
                await suite_signal(task, gate, timeout_s=0.05)
            if owner_fails:
                assert caught.value is error
    finally:
        task.cancel()
        with suppress(OSError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("operation", ["wait_signal", "open_gate"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_gate_absent_peer_releases_executor(tmp_path, operation, cancel):
    # A separate interpreter makes a stuck executor join an ordinary, bounded failure.
    helper_dir = str(Path(__file__).parent)
    script = f"import sys; sys.path.insert(0, {helper_dir!r})\n" + textwrap.dedent(f"""
        import asyncio
        from pathlib import Path
        from conftest import Gate

        async def check():
            gate = Gate(Path.cwd(), "absent")
            started = asyncio.Event()
            loop = asyncio.get_running_loop()
            to_thread = asyncio.to_thread

            async def observe(fn, *args, **kwargs):
                def worker():
                    loop.call_soon_threadsafe(started.set)
                    return fn(*args, **kwargs)
                return await to_thread(worker)

            asyncio.to_thread = observe
            wait = gate.{operation}() if {cancel!r} else gate.{operation}(timeout_s=0.05)
            task = asyncio.create_task(wait)
            if {cancel!r}:
                await started.wait()
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError if {cancel!r} else TimeoutError):
                pass
            else:
                raise AssertionError("missing peer completed")

        asyncio.run(check())
        print("executor closed")
    """)
    result = await run_process([sys.executable, "-c", script], cwd=tmp_path, timeout_s=15)
    assert not result.timed_out, result.stderr
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "executor closed"


async def test_cancel_sleeping_child_and_repeated_cleanup_cancellation(
    workspace, api, monkeypatch, gate_factory, wait_gone
):
    h, t = await candidate(workspace, api)
    cleaning = asyncio.Event()
    gate = gate_factory("inverse-suite")
    await workspace.check_isolation()
    partial = SuiteRun(
        SuiteResult(outcome="error", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]),
        "partial suite output",
    )

    async def sleeping_suite(**kwargs):
        try:
            await run_process(
                [
                    sys.executable,
                    "-c",
                    "import os, time; " + gate.child_signal + "; time.sleep(60)",
                ],
                cwd=kwargs["tree_root"],
                timeout_s=30,
            )
        except asyncio.CancelledError:
            raise SuiteInterrupted(partial) from None
        raise AssertionError("sleeping suite completed")

    monkeypatch.setattr("oversight.workspace.suite_guard.run_suite", sleeping_suite)
    real = workspace.git
    seen = []

    async def observe(tree, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            cleaning.set()
            await asyncio.sleep(0.03)
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", observe)

    async def owner():
        async with (
            workspace.locks(main=True, agent_id="agent-1"),
            api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ) as item,
        ):
            seen.append(item)
            await workspace.run_tests(item.tree, timeout_s=30, isolation="required")

    task = asyncio.create_task(owner())
    try:
        pid = await suite_signal(task, gate)
        task.cancel()
        async with asyncio.timeout(60):
            await cleaning.wait()
            task.cancel()
            with pytest.raises(SuiteInterrupted) as interrupted:
                await task
    finally:
        if not task.done():
            task.cancel()
            async with asyncio.timeout(60):
                with suppress(asyncio.CancelledError):
                    await task
    assert interrupted.value.run.output == partial.output
    assert interrupted.value.run.result == partial.result
    await asyncio.to_thread(wait_gone, pid)
    assert seen[0].cleaned
    assert not seen[0].tree.root.exists()
    assert not seen[0].tree.git_identity.directory.exists()
    assert workspace.operations.idle


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"outcome": "failed"}, "outcome"),
        ({"outcome": "error"}, "outcome"),
        ({"outcome": "timeout"}, "outcome"),
        ({"passed": 0}, "passed"),
        ({"failed": 1}, "failed"),
        ({"skipped": 1}, "skipped"),
        ({"problems": ("bad report",)}, "problems"),
        ({"tested_tree": "other"}, "tree"),
        ({"cleaned": False}, "cleanup"),
        ({}, None),
    ],
)
def test_suite_guard(api, change, reason):
    run = SuiteRun(
        SuiteResult(
            outcome="passed", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]
        ),
        "",
        problems=(),
        tested_tree="T",
    )
    result_fields = {
        k: v for k, v in change.items() if k in ("outcome", "passed", "failed", "skipped")
    }
    run = replace(
        run,
        result=SuiteResult.model_validate(run.result.model_dump() | result_fields),
        **{k: v for k, v in change.items() if k in ("problems", "tested_tree")},
    )
    problem = api.inverse_suite_problem(run, tree="T", cleaned=change.get("cleaned", True))
    assert problem is None if reason is None else reason in problem


async def prepared(workspace, api):
    h, t = await candidate(workspace, api)
    async with (
        workspace.locks(main=True, agent_id="agent-1"),
        api.prepare_inverse(
            workspace,
            agent_id="agent-1",
            main_commit=h,
            inverse_tree=t,
            message=MESSAGE,
            timeout_s=5,
        ) as item,
    ):
        pass
    return item


def effect_args(item):
    return dict(
        main_commit=item.main_commit,
        inverse_commit=item.inverse_commit,
        inverse_tree=item.inverse_tree,
        timeout_s=5,
    )


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
async def test_dirty_or_detached_refused_before_cas(workspace, api, dirty, monkeypatch):
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


async def test_partial_add_cancellation_removes_registration(workspace, api, monkeypatch):
    h, t = await candidate(workspace, api)
    real, roots = workspace.git, []

    async def interrupt(tree, *args, **kwargs):
        result = await real(tree, *args, **kwargs)
        if args[:2] == ("worktree", "add"):
            roots.append(Path(args[3]))
            raise asyncio.CancelledError
        return result

    monkeypatch.setattr(workspace, "git", interrupt)
    async with workspace.locks(main=True, agent_id="agent-1"):
        with pytest.raises(asyncio.CancelledError):
            async with api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ):
                pytest.fail("interrupted add yielded")
        assert roots and not roots[0].exists()
        assert not (workspace.main.git_identity.common / "worktrees" / roots[0].name).exists()
        assert str(roots[0]) not in await workspace.git_ok(
            workspace.main, "worktree", "list", "--porcelain"
        )


@pytest.mark.parametrize("prune_fails", [False, True])
async def test_cleanup_fallback_and_unverified_cleanup(workspace, api, monkeypatch, prune_fails):
    h, t = await candidate(workspace, api)
    real = workspace.git

    async def fail(tree, *args, **kwargs):
        if args[:2] == ("worktree", "remove") or (
            prune_fails and args[:2] == ("worktree", "prune")
        ):
            return ProcessResult(1, "", "injected cleanup error", False, False, 1)
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", fail)
    async with workspace.locks(main=True, agent_id="agent-1"):
        async with api.prepare_inverse(
            workspace,
            agent_id="agent-1",
            main_commit=h,
            inverse_tree=t,
            message=MESSAGE,
            timeout_s=5,
        ) as item:
            pass
        assert item.cleaned is (not prune_fails)
        assert not item.tree.root.exists()
        assert item.tree.git_identity.directory.exists() is prune_fails


@pytest.mark.parametrize("body_fails", [False, True])
async def test_cleanup_cancellation_preserves_body_exit(workspace, api, monkeypatch, body_fails):
    h, t = await candidate(workspace, api)
    real = workspace.git
    cleaning = asyncio.Event()
    interrupted = False

    async def interrupt(tree, *args, **kwargs):
        nonlocal interrupted
        if args[:2] == ("worktree", "remove") and not interrupted:
            interrupted = True
            cleaning.set()
            await asyncio.Event().wait()
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", interrupt)
    error = ValueError("body failed")
    seen = []

    async def owner():
        async with (
            workspace.locks(main=True, agent_id="agent-1"),
            api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ) as item,
        ):
            seen.append(item)
            if body_fails:
                raise error

    task = asyncio.create_task(owner())
    async with asyncio.timeout(60):
        await cleaning.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
    assert task.cancelled()
    assert caught.value.__cause__ is (error if body_fails else None)
    assert interrupted and seen[0].cleaned


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
