"""Object-only inversion, real disposable repositories, and fail-closed fault injection."""

import asyncio
from dataclasses import dataclass, field, replace
from typing import get_args

import pytest
from test_policy_profiles import PROFILE, policy_inputs

from oversight.changes._policy import assess
from oversight.changes._revert_preflight import preflight
from oversight.events import StoredEvent
from oversight.workspace import Author, ProcessResult, Workspace
from oversight_contracts.commands import RevertChange
from oversight_contracts.events import ChangeResolved, ChangeResolvedEvent
from oversight_contracts.policy import POLICY_RULES, ChangeAssessment, RuleAssessment

pytestmark = pytest.mark.anyio


@dataclass
class Repo:
    ws: Workspace
    event: ChangeResolvedEvent = field(init=False)
    command: RevertChange = field(init=False)
    parent: str = ""
    proposal: str = ""

    async def git(self, *args):
        async with self.ws.locks(main=True):
            return await self.ws.git_ok(self.ws.main, *args, timeout_s=10)

    async def commit(self, name, content):
        (self.ws.main.root / name).write_text(content)
        await self.git("add", name)
        await self.git("commit", "-qm", "test: edit file")
        return (await self.git("rev-parse", "HEAD")).strip()

    async def classify(self, event=None, command=None, change_id="change-1"):
        before = await self.snapshot()
        result = await preflight(self.ws, event or self.event, command or self.command, change_id)
        assert await self.snapshot() == before
        assert self.ws.operations.idle
        if result.status == "conflict":
            assert result.candidate_tree is None
        assert result.diagnostic
        return result

    async def snapshot(self):
        root = self.ws.main.root
        metadata = root / ".git"
        # Include all metadata except objects (the only permitted writes), including logs,
        # the full refs, raw index, sentinels and worktree registrations.
        administrative = {
            p.relative_to(metadata).as_posix(): p.read_bytes()
            for p in metadata.rglob("*")
            if p.is_file() and "objects" not in p.relative_to(metadata).parts
        }
        contents = {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts
        }
        return administrative, contents, await self.git("worktree", "list", "--porcelain")


@pytest.fixture
async def repo(tmp_path):
    runtime = tmp_path / ".runtime"
    (runtime / "workspace").mkdir(parents=True)
    ws = Workspace(runtime, tmp_path / "unused", pytest_pin="unused")
    async with ws.locks(main=True):
        await ws.git_ok(ws.main, "init", "-q", "-b", "main", "--template=", timeout_s=10)
    r = Repo(ws)
    r.parent = await r.commit("value.py", "value = 1\n")
    await r.git("branch", "proposal")
    # Setup may switch branches and merge; classification must never do either.
    await r.git("checkout", "-q", "proposal")
    r.proposal = await r.commit("value.py", "value = 2\n")
    await r.git("checkout", "-q", "main")
    async with ws.locks(main=True):
        await ws.git_ok(
            ws.main,
            "merge",
            "--no-ff",
            "-qm",
            "Merge change-1 (agent-1)\n\nMerged-by: rules",
            r.proposal,
            author=Author("rules", "rules@oversight.invalid"),
            timeout_s=10,
        )
    merge = (await r.git("rev-parse", "HEAD")).strip()
    tree = (await r.git("rev-parse", "HEAD^{tree}")).strip()
    inputs = policy_inputs()
    assert inputs.review is not None
    review = inputs.review.model_copy(
        update=dict(
            base_commit=r.parent, main_commit=r.parent, head_commit=r.proposal, candidate_tree=tree
        )
    )
    assessment = assess(
        replace(
            policy_inputs(),
            base_commit=r.parent,
            main_commit=r.parent,
            head_commit=r.proposal,
            candidate_tree=tree,
            review=review,
        ),
        PROFILE,
    )
    r.event = ChangeResolvedEvent(
        seq=500,
        ts="2026-10-29T00:00:00.000Z",
        type="change.resolved",
        payload=ChangeResolved(
            change_id="change-1",
            agent_id="agent-1",
            revision=1,
            decision="approved",
            reason=None,
            comment=None,
            merge="merged",
            merge_commit=merge,
            conflict=None,
            decided_by="rules",
            head_commit=r.proposal,
            assessment=assessment,
            reasons=["All rules passed."],
        ),
    )
    r.command = RevertChange(resolution_seq=500, merge_commit=merge, main_commit=merge)
    return r


def altered(repo, **updates):
    # Deliberately bypass validation: preflight must distrust invalid/unknown evidence too.
    payload = repo.event.payload.model_copy(update=updates)
    return repo.event.model_copy(update={"payload": payload})


@pytest.mark.parametrize(
    "updates",
    [
        {"decided_by": "person"},
        {"decided_by": None},
        {"decided_by": "future"},
        {"decision": "future"},
        {"decision": "rejected"},
        {"decision": "changes_requested"},
        {"merge": None},
        {"merge": "conflict"},
        {"merge": "future"},
        {"merge_commit": "0" * 40},
        {"assessment": None},
        {"head_commit": "0" * 40},
        {"change_id": "change-2"},
        {"agent_id": "agent-2"},
        {"revision": 2},
        {"reasons": []},
    ],
)
async def test_refuse_resolution(repo, updates):
    assert (await repo.classify(event=altered(repo, **updates))).status == "conflict"


@pytest.mark.parametrize(
    "field,value",
    [
        ("resolution_seq", 499),
        ("merge_commit", "0" * 40),
        ("main_commit", "0" * 40),
        ("merge_commit", "HEAD"),
        ("main_commit", "--all"),
    ],
)
async def test_refuse_command(repo, field, value):
    command = repo.command.model_copy(update={field: value})
    assert (await repo.classify(command=command)).status == "conflict"


@pytest.mark.parametrize("later", [False, True])
async def test_clean_candidate_preserves_later_edits(repo, later):
    if later:
        head = await repo.commit("later.py", "unrelated = True\n")
        repo.command = repo.command.model_copy(update={"main_commit": head})
    result = await repo.classify()
    assert result.status == "clean"
    assert result.main_commit == repo.command.main_commit
    assert result.merge_commit == repo.command.merge_commit
    assert await repo.git("show", f"{result.candidate_tree}:value.py") == "value = 1\n"
    if later:
        assert await repo.git("show", f"{result.candidate_tree}:later.py") == "unrelated = True\n"


@pytest.mark.parametrize(
    "gap", [None, "probes", "missing", "duplicate", "hit", "incomplete", "unsupported"]
)
async def test_revert_preflight_accepts_nine_consistent_rules_only(repo, gap):
    assessment = repo.event.payload.assessment.model_copy(deep=True)
    assert len(assessment.rules) == 9 and assessment.policy_version == "safe-merge-paths-v1"
    if gap == "probes":
        assessment = assessment.model_copy(
            update={
                "policy_version": "safe-merge-probes-v1",
                "rules": [r for r in assessment.rules if r.rule != "protected_paths"],
            }
        )
    elif gap == "missing":
        assessment.rules.pop()
    elif gap == "duplicate":
        assessment.rules[-1] = assessment.rules[0]
    elif gap in {"hit", "incomplete"}:
        item = next(r for r in assessment.rules if r.rule == "protected_paths")
        # Keep the claimed pass: preflight must cross-check its observations.
        item.observed.update(violations=int(gap == "hit"), complete=gap != "incomplete")
    elif gap == "unsupported":
        assessment = assessment.model_copy(update={"policy_version": "future-paths"})
    result = await repo.classify(event=altered(repo, assessment=assessment))
    assert result.status == ("clean" if gap is None else "conflict")


async def test_overlap_conflicts(repo):
    head = await repo.commit("value.py", "value = 3\n")
    repo.command = repo.command.model_copy(update={"main_commit": head})
    assert (await repo.classify()).status == "conflict"


@pytest.mark.parametrize("kind", ["staged", "unstaged", "untracked", "all"])
async def test_dirty_checkout_untouched(repo, kind):
    if kind in {"staged", "all"}:
        (repo.ws.main.root / "staged.py").write_text("staged = True\n")
        await repo.git("add", "staged.py")
    if kind in {"unstaged", "all"}:
        (repo.ws.main.root / "value.py").write_text("uncommitted = True\n")
    if kind in {"untracked", "all"}:
        (repo.ws.main.root / "untracked.py").write_text("untracked = True\n")
    assert (await repo.classify()).status == "conflict"


@pytest.mark.parametrize("updates", [{"type": "future"}, {"seq": 499}, {"seq": 0}])
async def test_event_identity(repo, updates):
    assert (await repo.classify(event=repo.event.model_copy(update=updates))).status == "conflict"


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_tree", None),
        ("candidate_tree", "0" * 40),
        ("head_commit", "0" * 40),
        ("main_commit", "0" * 40),
        ("policy_version", "future"),
        ("mode", "future"),
    ],
)
async def test_assessment_uncertainty(repo, field, value):
    assessment = repo.event.payload.assessment.model_copy(update={field: value})
    assert (await repo.classify(event=altered(repo, assessment=assessment))).status == "conflict"


@pytest.mark.parametrize("value", ["fail", "unknown", "future"])
async def test_unknown_rule_results(repo, value):
    assessment = repo.event.payload.assessment.model_copy(deep=True)
    object.__setattr__(assessment.rules[0], "result", value)
    assert (await repo.classify(event=altered(repo, assessment=assessment))).status == "conflict"


@pytest.mark.parametrize(
    "kind", ["absent", "nonancestor", "one", "three", "reversed", "actor", "message"]
)
async def test_unverifiable_merge(repo, kind):
    tree = repo.event.payload.assessment.candidate_tree
    if kind == "absent":
        merge = "0" * 40
    else:
        parents = [repo.parent, repo.proposal]
        if kind == "one":
            parents = [repo.proposal]
        elif kind == "three":
            parents.append(repo.command.merge_commit)
        elif kind == "reversed":
            parents.reverse()
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        args += [
            "-m",
            "wrong" if kind == "message" else "Merge change-1 (agent-1)\n\nMerged-by: rules",
        ]
        async with repo.ws.locks(main=True):
            merge = (
                await repo.ws.git_ok(
                    repo.ws.main,
                    *args,
                    timeout_s=10,
                    author=Author("Other", "other@oversight.invalid")
                    if kind == "actor"
                    else Author("rules", "rules@oversight.invalid"),
                    date="2026-10-28T00:00:00Z",
                )
            ).strip()
        if kind != "nonancestor":
            await repo.git("update-ref", "refs/heads/main", merge)
            repo.command = repo.command.model_copy(update={"main_commit": merge})
    repo.command = repo.command.model_copy(update={"merge_commit": merge})
    assert (await repo.classify(event=altered(repo, merge_commit=merge))).status == "conflict"


@pytest.mark.parametrize(
    "marker", ["MERGE_HEAD", "rebase-merge", "sequencer", "REVERT_HEAD", "index.lock"]
)
async def test_in_progress_untouched(repo, marker):
    (repo.ws.main.root / ".git" / marker).write_text(repo.proposal + "\n")
    assert (await repo.classify()).status == "conflict"


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
async def test_hidden_dirty_checkout(repo, flag):
    await repo.git("update-index", flag, "value.py")
    (repo.ws.main.root / "value.py").write_text("hidden = True\n")
    assert (await repo.classify()).status == "conflict"


@pytest.mark.parametrize(
    "fault",
    [
        "unsupported",
        "malformed",
        "extra",
        "no_newline",
        "timeout",
        "truncated",
        "signal",
        "unreaped",
        "stderr",
        "exception",
        "integrity",
        "changed_main",
        "bad_tree",
        "bad_fsck",
        "incomplete_metadata",
    ],
)
async def test_uncertain_process_never_clean(repo, monkeypatch, fault):
    from oversight.workspace import IntegrityError

    original = repo.ws.git
    before = await repo.snapshot()
    calls = []
    heads = 0

    async def injected(tree, *args, timeout_s=30, **kwargs):
        nonlocal heads
        assert repo.ws.operations.holds(main=True)
        assert repo.ws.operations._git.owner is asyncio.current_task()
        assert 0 < timeout_s <= 30
        calls.append(args)
        if args == ("rev-parse", "--verify", "HEAD^{commit}"):
            heads += 1
            if fault == "changed_main" and heads == 2:
                return ProcessResult(0, repo.parent + "\n", "", False, False, 0)
        if args[0] == "show" and fault == "incomplete_metadata":
            result = await original(tree, *args, timeout_s=timeout_s, **kwargs)
            return replace(result, stdout=result.stdout.rstrip("\n"))
        if args[0] == "fsck" and fault == "bad_fsck":
            return ProcessResult(0, "uncertain\n", "", False, False, 0)
        if args[0] == "cat-file" and fault == "bad_tree":
            return ProcessResult(0, "blob\n", "", False, False, 0)
        if args[0] == "merge-tree":
            if fault == "exception":
                raise TimeoutError
            if fault == "integrity":
                raise IntegrityError("injected", ".")
            if fault not in {"changed_main", "bad_tree", "bad_fsck", "incomplete_metadata"}:
                return ProcessResult(
                    129
                    if fault == "unsupported"
                    else -9
                    if fault == "signal"
                    else None
                    if fault == "unreaped"
                    else 0,
                    "malformed\n"
                    if fault == "malformed"
                    else "f" * 40
                    + ("\nextra\n" if fault == "extra" else "" if fault == "no_newline" else "\n"),
                    "unsupported"
                    if fault == "unsupported"
                    else "uncertain"
                    if fault == "stderr"
                    else "",
                    fault == "timeout",
                    fault == "truncated",
                    0,
                )
        return await original(tree, *args, timeout_s=timeout_s, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(repo.ws, "git", injected)
        result = await preflight(repo.ws, repo.event, repo.command, "change-1")
    assert result.status == "conflict" and result.candidate_tree is None
    assert repo.ws.operations.idle
    assert await repo.snapshot() == before
    assert any(
        args[0] == ("show" if fault == "incomplete_metadata" else "merge-tree") for args in calls
    )
    assert {args[0] for args in calls} <= {
        "symbolic-ref",
        "rev-parse",
        "status",
        "ls-files",
        "merge-base",
        "show",
        "merge-tree",
        "cat-file",
        "fsck",
    }


async def test_cancellation_retains_locks_through_process_cleanup(repo, monkeypatch):
    from oversight.workspace import core, run_process

    original = run_process
    entered, cleaned = asyncio.Event(), asyncio.Event()
    before = await repo.snapshot()

    async def held(argv, **kwargs):
        if "merge-tree" not in argv:
            return await original(argv, **kwargs)
        # Exercise the actual bounded process runner with a sleeping child, no provider.
        import sys

        entered.set()
        try:
            return await run_process(
                [sys.executable, "-c", "import time; time.sleep(60)"], **kwargs
            )
        finally:
            assert repo.ws.operations.holds(main=True)
            assert repo.ws.operations._git.owner is asyncio.current_task()
            cleaned.set()

    monkeypatch.setattr(core, "run_process", held)
    task = asyncio.create_task(preflight(repo.ws, repo.event, repo.command, "change-1"))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert cleaned.is_set() and repo.ws.operations.idle
    assert await repo.snapshot() == before


async def test_main_lock_wait_is_bounded(repo):
    async with repo.ws.locks(main=True):
        task = asyncio.create_task(
            preflight(repo.ws, repo.event, repo.command, "change-1", timeout_s=0.01)
        )
        result = await task
    assert result.status == "conflict" and repo.ws.operations.idle


@pytest.mark.parametrize("field", [None, "seq", "type", "ts", "envelope"])
async def test_stored_event(repo, field):
    event = StoredEvent(
        repo.event.seq, repo.event.ts, repo.event.type, repo.event.model_dump_json()
    )
    if field:
        event = replace(event, **{field: 499 if field == "seq" else "invalid"})
    result = await repo.classify(event=event)
    assert result.status == ("conflict" if field else "clean")


@pytest.mark.parametrize(
    "name,update",
    [
        ("combined_tests", {"failed": 1}),
        ("small_cone", {"nodes": 6}),
        ("small_change", {"files": 4}),
        ("known_spend", {"spent_usd": 1.0}),
        ("existing_tests_untouched", {"modified_existing_paths": ["value.py"]}),
        ("cross_review", {"complete": False}),
        ("test_integrity", {"violations": 1}),
        ("added_tests_fail_before", {"violations": 1}),
    ],
)
async def test_inconsistent_pass_observations(repo, name, update):
    assessment = repo.event.payload.assessment.model_copy(deep=True)
    rule = next(r for r in assessment.rules if r.rule == name)
    rule.observed.update(update)
    assert (await repo.classify(event=altered(repo, assessment=assessment))).status == "conflict"


async def test_git_lease_wait_is_bounded(repo):
    entered, release = asyncio.Event(), asyncio.Event()

    async def occupy():
        async with repo.ws.locks(agent_id="agent-1"), repo.ws.git_lease():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(occupy())
    await entered.wait()
    try:
        result = await preflight(repo.ws, repo.event, repo.command, "change-1", timeout_s=0.01)
        assert result.status == "conflict"
    finally:
        release.set()
        await holder
    assert repo.ws.operations.idle


@pytest.mark.parametrize("branch", ["proposal", "--detach"])
async def test_head_must_be_attached_to_main(repo, branch):
    await repo.git("checkout", "-q", branch)
    assert (await repo.classify()).status == "conflict"


async def test_untyped_event_refused(repo):
    before = await repo.snapshot()
    result = await preflight(repo.ws, None, repo.command, "change-1")
    assert result.status == "conflict" and result.candidate_tree is None
    assert await repo.snapshot() == before


@pytest.mark.parametrize(
    "model,field,expected",
    [
        (ChangeAssessment, "mode", {"ask_me", "merge_when_safe"}),
        (RuleAssessment, "result", {"pass", "fail", "unknown"}),
        (
            RuleAssessment,
            "rule",
            set(POLICY_RULES[PROFILE])
            | {"run_test_integrity", "tests_reach_changes", "within_zone", "protected_paths"},
        ),
        (ChangeResolved, "decision", {"approved", "changes_requested", "rejected"}),
        (ChangeResolved, "merge", {"merged", "conflict"}),
        (ChangeResolved, "decided_by", {"person", "rules", "cross_review", "lead_agent"}),
    ],
)
def test_resolution_enum_consumers_are_complete(model, field, expected):
    def values(annotation):
        annotation = getattr(annotation, "__value__", annotation)
        return {
            v
            for arg in get_args(annotation)
            for v in ([arg] if isinstance(arg, str) else values(arg))
        }

    assert values(model.model_fields[field].annotation) == expected
