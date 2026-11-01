"""Real Git/pytest probes, their exact event references and conservative failure paths."""

import asyncio
from dataclasses import replace

import pytest
from changes_helpers import EDIT, TESTS, Harness
from decision_helpers import observed_target

from oversight.changes._facts import FactsEvidence, validate_change_facts
from oversight.changes._probes import comparison
from oversight.changes._summary_capture import capture_summary
from oversight.workspace import IsolationPolicy, base_probe_checkout
from oversight_contracts.events import CaseResult, SuiteExecution, SuiteResult
from oversight_contracts.review import ChangeFacts, ChangeTarget


@pytest.fixture
def session_c() -> bool:
    return True


async def add_test(h: Harness, text: str) -> None:
    old = (await h.workspace.worktree("agent-1").read_text(TESTS)).content
    assert await h.edit(1, TESTS, old, old + "\n\n" + text)


def assert_clean(h: Harness) -> None:
    assert not list(h.workspace.tmp_dir.glob("base-probe-*"))
    assert not list(h.workspace.tmp_dir.glob("suite-index-*"))


@pytest.mark.anyio
async def test_nt_wiring_emits_unavailable_without_probe_work(h: Harness, monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from oversight.changes import _probes

    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    checkout, discovery, run = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(_probes, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(_probes, "base_probe_checkout", checkout)
    monkeypatch.setattr(_probes, "added_cases", discovery)
    monkeypatch.setattr(_probes, "_run", run)
    before = len(h.workspace.calls)
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert not facts.added_tests
    for phase in ("base_probe", "head_probe"):
        assert f"{phase} unavailable: native Windows isolation is unavailable" in facts.problems
    assert not any(e.phase.endswith("_probe") for e in h.payloads(SuiteExecution))
    assert not any(
        c.args[0] in {"checkout", "restore"} or "--detach" in c.args
        for c in h.workspace.calls[before:]
    )
    for spy in (checkout, discovery, run):
        spy.assert_not_called()
    assert_clean(h)


@pytest.mark.anyio
async def test_probes_overlay_tests_only_on_recorded_base_and_repeat_each_revision(
    h: Harness,
) -> None:
    await h.open(1)
    assert await h.edit(1, *EDIT)
    await add_test(h, "def test_added():\n    assert value() == 3\n")
    # Main moves independently; the probe must keep agent 1's original base.
    await h.run_agent(2, ("shop/mod.py", "return 1", "return 2"))
    await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    for revision in (1, 2):
        assert await h.finish(1)
        facts = [f for f in h.payloads(ChangeFacts) if f.change_id == "change-1"][-1]
        assert len(facts.added_tests) == 1, facts.problems
        [probe] = facts.added_tests
        assert facts.revision == revision and probe.result == "fails_before_passes_after"
        events = h.events()
        proposed = next(
            e
            for e in events
            if e.type == "change.proposed"
            and e.payload.change_id == "change-1"
            and e.payload.revision == revision
        )
        assert proposed.seq < probe.before_seq < probe.after_seq
        before, after = (events[seq - 1].payload for seq in (probe.before_seq, probe.after_seq))
        assert before.phase == "base_probe" and after.phase == "head_probe"
        assert before.tested_commit is None  # Base implementation plus head's test file.
        assert before.tested_tree != facts.candidate_tree
        assert (
            after.tested_tree == facts.candidate_tree and after.tested_commit == facts.head_commit
        )
        assert [c.test_id for c in before.suite.cases] == [probe.test_id]
        assert [c.test_id for c in after.suite.cases] == [probe.test_id]
        assert probe.before_seq in facts.test_runs and probe.after_seq in facts.test_runs
        assert not any("package 4" in p or "added tests:" in p for p in facts.problems)
        assert not any(e.type == "agent.step" for e in events)
        assert_clean(h)
        if revision == 1:
            await h.manager.request_changes(
                "change-1", "Again", **observed_target(h.manager, "change-1")
            )
    calls = [c for c in h.workspace.calls if c.args[0] == "worktree"]
    assert all("main" in c.held and "agent-1" in c.held for c in calls if "--detach" in c.args)
    listing = await h.main_git("worktree", "list", "--porcelain")
    assert "base-probe-" not in listing.stdout


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("assert value() > 0", "passes_before_and_after"),
        ("assert value() == 99", "fails_after"),
        ("import pytest; pytest.skip('pending')", "skipped"),
    ],
)
async def test_real_case_outcomes(h: Harness, body: str, expected: str) -> None:
    await h.open(1)
    await add_test(h, f"def test_added():\n    {body}\n")
    assert await h.finish(1)
    assert h.payloads(ChangeFacts)[-1].added_tests[0].result == expected
    assert_clean(h)


@pytest.mark.anyio
async def test_new_file_with_parameters_and_support(h: Harness) -> None:
    tree = await h.open(1)
    assert await h.edit(1, *EDIT)
    for path, content in {
        "tests/conftest.py": "import pytest\n@pytest.fixture\ndef expected(): return 3\n",
        "tests/test_added.py": (
            "import pytest\nfrom shop.mod import value\n"
            "@pytest.mark.parametrize('x', [1, 2], ids=['one', 'two'])\n"
            "def test_added(x, expected):\n    assert value() == expected\n"
        ),
    }.items():
        await tree.write_text(path, content, expected_hash=None)
        assert await h.manager.file_edited("agent-1", path)
    assert await h.finish(1)
    probes = h.payloads(ChangeFacts)[-1].added_tests
    assert [p.test_id for p in probes] == [
        "tests/test_added.py::test_added[one]",
        "tests/test_added.py::test_added[two]",
    ]
    assert all(p.result == "fails_before_passes_after" for p in probes)
    assert_clean(h)


@pytest.mark.anyio
async def test_base_setup_failure_omits_probe_without_inventing_seqs(h: Harness) -> None:
    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    h.workspace.arm("worktree", mode="after")  # add succeeds, receipt says timeout
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert not facts.added_tests
    assert any("base_probe unavailable" in p for p in facts.problems)
    assert any(e.phase == "head_probe" for e in h.payloads(SuiteExecution))
    assert_clean(h)


@pytest.mark.anyio
async def test_timeout_keeps_partial_execution_and_unknown(h: Harness, monkeypatch) -> None:
    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    original = h.workspace.run_tests

    async def timeout(tree, *, timeout_s, case_ids=(), isolation: IsolationPolicy = "best_effort"):
        run = await original(tree, timeout_s=timeout_s, case_ids=case_ids, isolation=isolation)
        if case_ids and tree.root != h.worktree_dir("agent-1"):
            return replace(
                run,
                result=run.result.model_copy(update={"outcome": "timeout"}),
                problems=("suite timed out",),
            )
        return run

    monkeypatch.setattr(h.workspace, "run_tests", timeout)
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert facts.added_tests[0].result == "unknown"
    assert any("suite timed out" in p for p in facts.problems)
    assert_clean(h)


@pytest.mark.anyio
async def test_checkout_cleans_on_cancel(h: Harness) -> None:
    tree = await h.open(1)
    entered = asyncio.Event()

    async def attempt():
        async with (
            h.workspace.locks(main=True, agent_id="agent-1"),
            base_probe_checkout(
                h.workspace,
                agent=tree,
                base=h.base_commit,
                head=h.base_commit,
                paths=(TESTS,),
                timeout_s=5,
            ),
        ):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(attempt())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_clean(h)
    assert "base-probe-" not in (await h.main_git("worktree", "list", "--porcelain")).stdout


@pytest.mark.anyio
async def test_validator_rejects_forged_probe_result_and_reference(h: Harness) -> None:
    from oversight.changes._shape_facts import ShapeCapture

    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    rec = h.manager._agents["agent-1"]
    evidence = FactsEvidence(
        rec.agent_id,
        ChangeTarget.model_validate(facts.model_dump(include=set(ChangeTarget.model_fields))),
        rec.declared,
        tuple(facts.edited_paths),
        tuple(facts.integrity),
        tuple(facts.problems),
        rec.executions,
        rec.suite_runs,
        tuple(facts.added_tests),
        summary_evidence=capture_summary(
            rec, added_test_ids=tuple(probe.test_id for probe in facts.added_tests)
        ),
        reach=facts.reach,
        shape=ShapeCapture(
            tuple(facts.shape) if facts.shape is not None else None,
            facts.shape_complete,
            tuple(problem for problem in facts.problems if problem.startswith("shape:")),
        ),
    )
    validate_change_facts(facts, evidence)
    for update in ({"result": "fails_before_passes_after"}, {"before_seq": facts.test_runs[0]}):
        forged = facts.added_tests[0].model_copy(update=update)
        with pytest.raises(ValueError):
            validate_change_facts(
                facts.model_copy(update={"added_tests": [forged]}),
                replace(evidence, added_tests=(forged,)),
            )


@pytest.mark.parametrize("incomplete", [False, True])
def test_absence_and_truncation_do_not_prove_success(incomplete: bool) -> None:
    def execution(phase, cases):
        return SuiteExecution(
            change_id="change-1",
            agent_id="agent-1",
            revision=1,
            phase=phase,
            tested_commit=None,
            tested_tree="tree",
            suite=SuiteResult(
                outcome="passed",
                passed=1,
                failed=0,
                skipped=0,
                duration_ms=0,
                failed_tests=[],
                cases=cases,
                cases_complete=not incomplete,
            ),
        )

    test_id = "tests/test_x.py::test_added"
    before = execution("base_probe", [CaseResult(test_id=test_id, outcome="failed")])
    after = execution("head_probe", [])
    assert comparison((test_id,), before, after) == (
        (test_id, "unknown" if incomplete else "not_collected_after"),
    )


@pytest.mark.anyio
async def test_side_deadline_and_external_cancel_preserve_execution(
    h: Harness, monkeypatch
) -> None:
    from oversight.workspace import IsolationPolicy, SuiteInterrupted

    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    rec = h.manager._agents["agent-1"]
    rec.task = replace(rec.task, limits=rec.task.limits.model_copy(update={"test_timeout_s": 2}))
    original = h.workspace.run_tests
    entered = asyncio.Event()

    async def interrupted(
        tree, *, timeout_s, case_ids=(), isolation: IsolationPolicy = "best_effort"
    ):
        run = await original(tree, timeout_s=timeout_s, case_ids=case_ids, isolation=isolation)
        if case_ids and tree.root != h.worktree_dir("agent-1"):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                raise SuiteInterrupted(run) from error
        return run

    monkeypatch.setattr(h.workspace, "run_tests", interrupted)
    # A deadline still publishes both sides and a conservative unknown.
    async with asyncio.timeout(10):
        assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert facts.added_tests[0].result == "unknown"
    assert any("probe deadline exceeded" in p for p in facts.problems)
    assert_clean(h)
    await h.manager.request_changes("change-1", "Again", **observed_target(h.manager, "change-1"))
    entered.clear()
    task = asyncio.create_task(h.finish(1))
    async with asyncio.timeout(10):
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    facts = h.payloads(ChangeFacts)[-1]
    assert facts.revision == 2 and not facts.added_tests
    assert "added tests: probes interrupted" in facts.problems
    assert h.payloads(SuiteExecution)[-1].phase == "base_probe"
    h.manager.run_ended("agent-1", None)
    assert_clean(h)


@pytest.mark.anyio
async def test_duplicate_definitions_are_ambiguous_without_success(h: Harness) -> None:
    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n\ndef test_added():\n    assert True\n")
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert not facts.added_tests
    assert any("duplicate definitions" in p for p in facts.problems)
    assert not any(e.phase.endswith("_probe") for e in h.payloads(SuiteExecution))


@pytest.mark.anyio
async def test_cancelled_proposal_receipt_still_records_incomplete_facts(h: Harness) -> None:
    from oversight_contracts.events import ChangeProposed

    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    h.hold = ChangeProposed
    task = asyncio.create_task(h.finish(1))
    async with asyncio.timeout(10):
        await h.held.wait()
        task.cancel()
        h.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    facts = h.payloads(ChangeFacts)[-1]
    assert facts.revision == 1 and not facts.added_tests
    assert "added tests: probes interrupted" in facts.problems
    h.manager.run_ended("agent-1", None)
    assert_clean(h)


@pytest.mark.anyio
async def test_cleanup_fault_keeps_execution_and_filesystem_fallback(h: Harness) -> None:
    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    h.workspace.arm("worktree", skip=1)  # Fail remove after add; safe fallback still deletes.
    assert await h.finish(1)
    [probe] = h.payloads(ChangeFacts)[-1].added_tests
    assert probe.result == "passes_before_and_after"
    assert_clean(h)
    assert "base-probe-" not in (await h.main_git("worktree", "list", "--porcelain")).stdout


@pytest.mark.anyio
async def test_fixture_parameters_without_base_evidence_stay_explicitly_unknown(h: Harness) -> None:
    tree = await h.open(1)
    await tree.write_text(
        "tests/conftest.py",
        "import pytest\n@pytest.fixture(params=[1, 2], autouse=True)\n"
        "def sample(request):\n    return request.param\n",
        expected_hash=None,
    )
    assert await h.manager.file_edited("agent-1", "tests/conftest.py")
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert not facts.added_tests
    assert "added tests: base parameter ids unavailable for tests/conftest.py" in facts.problems


@pytest.mark.anyio
async def test_head_mutated_by_suite_cannot_certify_the_proposed_tree(
    h: Harness, monkeypatch
) -> None:
    await h.open(1)
    await add_test(h, "def test_added():\n    assert True\n")
    original = h.workspace.run_tests

    async def mutate_after_suite(
        tree, *, timeout_s, case_ids=(), isolation: IsolationPolicy = "best_effort"
    ):
        run = await original(tree, timeout_s=timeout_s, case_ids=case_ids, isolation=isolation)
        if not case_ids:
            source = await tree.read_text("shop/mod.py")
            await tree.write_text(
                "shop/mod.py", source.content + "# suite side effect\n", expected_hash=source.hash
            )
        return run

    monkeypatch.setattr(h.workspace, "run_tests", mutate_after_suite)
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    assert not facts.added_tests
    assert "added tests: head worktree differs from proposed commit" in facts.problems
    assert any(e.phase == "head_probe" for e in h.payloads(SuiteExecution))
    assert_clean(h)
