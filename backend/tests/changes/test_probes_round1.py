"""Regressions for ambiguous ids, deleted overlays and event-loop parsing."""

import asyncio
import threading

import pytest
from changes_helpers import EDIT, TESTS, Harness
from test_probes_wiring import add_test, assert_clean

from oversight.changes import _probes, _proposal
from oversight.workspace import IsolationPolicy
from oversight_contracts.events import ChangeProposed, SuiteExecution
from oversight_contracts.review import ChangeFacts


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["tests/conftest.py", TESTS])
async def test_added_case_with_ambiguous_fixture_ids_is_not_probed(h: Harness, path: str) -> None:
    tree = await h.open(1)
    fixture = (
        "import pytest\n@pytest.fixture(params=[1, 2], autouse=True)\n"
        "def sample(request):\n    return request.param\n"
    )
    if path == TESTS:
        await add_test(h, fixture)
    else:
        await tree.write_text(path, fixture, expected_hash=None)
        assert await h.manager.file_edited("agent-1", path)
    await add_test(h, "def test_added():\n    assert True\n")
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert not facts.added_tests  # No plain id or guessed expanded id can imply a result.
    assert f"added tests: base parameter ids unavailable for {path}" in facts.problems
    assert not any(e.phase.endswith("_probe") for e in h.payloads(SuiteExecution))
    assert_clean(h)


@pytest.mark.anyio
async def test_deleted_test_and_support_files_are_absent_from_base_probe(
    h: Harness, monkeypatch
) -> None:
    paths = ("tests/conftest.py", "tests/support.py", "tests/test_deleted.py")
    async with h.workspace.locks(main=True):
        for path in paths:
            await h.workspace.main.write_text(path, "# obsolete support\n", expected_hash=None)
        await h.workspace.git_ok(h.workspace.main, "add", "--", *paths)
        await h.workspace.git_ok(h.workspace.main, "commit", "-m", "Add obsolete test support")
    tree = await h.open(1)
    for path in paths:
        await asyncio.to_thread((tree.root / path).unlink)
        assert await h.manager.file_edited("agent-1", path)
    assert await h.edit(1, *EDIT)
    await add_test(h, "def test_added():\n    assert value() == 3\n")
    original = h.workspace.run_tests
    checked = []

    async def check_overlay(
        tree, *, timeout_s, case_ids=(), isolation: IsolationPolicy = "best_effort"
    ):
        if case_ids and tree.root != h.worktree_dir("agent-1"):
            checked.append(tree.root)
            assert all(not (tree.root / path).exists() for path in paths)
        return await original(tree, timeout_s=timeout_s, case_ids=case_ids, isolation=isolation)

    monkeypatch.setattr(h.workspace, "run_tests", check_overlay)
    assert await h.finish(1)
    [probe] = h.payloads(ChangeFacts)[-1].added_tests
    assert probe.result == "fails_before_passes_after"
    assert len(checked) == 1
    assert_clean(h)


@pytest.mark.anyio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_added_case_analysis_runs_off_the_event_loop(
    h: Harness, monkeypatch, interrupted: bool
) -> None:
    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    loop_thread = threading.get_ident()
    threads = []
    original = _probes.added_cases

    def capture_thread(rec, revision):
        threads.append(threading.get_ident())
        return original(rec, revision)

    monkeypatch.setattr(_probes, "added_cases", capture_thread)
    monkeypatch.setattr(_proposal, "added_cases", capture_thread)
    if interrupted:
        h.hold = ChangeProposed
        task = asyncio.create_task(h.finish(1))
        async with asyncio.timeout(10):
            await h.held.wait()
            task.cancel()
            h.release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        h.manager.run_ended("agent-1", None)
    else:
        assert await h.finish(1)
    assert threads and all(thread != loop_thread for thread in threads)
    facts = h.payloads(ChangeFacts)[-1]
    if interrupted:
        assert not facts.added_tests and "added tests: probes interrupted" in facts.problems
    else:
        assert facts.added_tests[0].result == "passes_before_and_after"
