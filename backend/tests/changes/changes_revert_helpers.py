"""Shared support moved verbatim from the split test modules."""

import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from test_revert_preflight import Repo
from test_revert_preflight import repo as repo

from oversight.changes import (
    ChangeManager,
    Person,
    RevertService,
    RevertTarget,
)
from oversight.events import EventStore
from oversight.graph import WorkspaceGraph
from oversight_contracts.commands import RevertChange
from oversight_contracts.policy import EvidenceRef

pytestmark = pytest.mark.anyio


@dataclass
class ServiceRepo:
    repo: Repo
    service: RevertService
    manager: ChangeManager
    target: RevertTarget
    command: RevertChange
    store: EventStore
    events: list
    policies: list

    @property
    def ws(self):
        return self.repo.ws

    async def publish(self, payload):
        return await self.service.ctx.publish(payload)

    async def run(self, **kwargs):
        principal = kwargs.pop("principal", Person)
        return await self.service.revert(self.target, self.command, principal=principal, **kwargs)

    async def head(self):
        if self.manager.main_suspect:
            # Observe the disposable repository without bypassing the production suspension latch.
            return (self.ws.main.root / ".git/refs/heads/main").read_text().strip()
        return (await self.repo.git("rev-parse", "HEAD")).strip()

    async def clean(self):
        assert await self.repo.git("status", "--porcelain", "-z") == ""
        assert not list(self.ws.tmp_dir.glob("revert-*"))
        listing = await self.repo.git("worktree", "list", "--porcelain")
        assert "revert-" not in listing
        assert self.ws.operations.idle


@pytest.fixture
async def system(repo, tmp_path, monkeypatch, request):
    root = repo.ws.main.root
    (root / "tests").mkdir()
    h = await repo.commit(
        "tests/test_value.py",
        "from pathlib import Path\n\ndef test_inverse():\n"
        '    assert Path("value.py").read_text() == "value = 1\\n"\n',
    )
    graph = WorkspaceGraph(root)
    snapshot = graph.scan(h)
    run_id = getattr(request, "param", "run-test")
    store = EventStore(tmp_path / "service.sqlite", run_id)
    await store.start()
    events, policies = [], []

    async def publish(payload):
        event = await store.publish(payload)
        events.append(event)
        return event

    await publish(snapshot)
    payload = repo.event.payload.model_copy(deep=True)
    for rule in payload.assessment.rules:
        rule.evidence[:] = [EvidenceRef(git_object=repo.proposal)]
    resolution = await publish(payload)
    command = repo.command.model_copy(update=dict(main_commit=h, resolution_seq=resolution.seq))
    manager = ChangeManager(repo.ws, publish, main_graph=graph, main_snapshot=snapshot)
    service = RevertService(ctx=manager._ctx, run_id=run_id, test_timeout_s=10)
    repo.ws._demo_python = Path(sys.executable)

    def isolated(policy, injected, open_runner):
        # Only the namespace adapter is scripted. Real suite, reports and Git guards remain.
        policies.append(policy)
        assert policy == "required"
        assert repo.ws.operations.holds(main=True, agent_id="agent-1")
        assert not manager._ctx.mode._lock.locked()
        return open_runner

    monkeypatch.setattr(repo.ws._isolation, "select", isolated)
    s = ServiceRepo(
        repo,
        service,
        manager,
        RevertTarget(run_id, "change-1", resolution),
        command,
        store,
        events,
        policies,
    )
    try:
        yield s
    finally:
        await service.aclose()
        await manager.aclose()
        await store.close()


async def install_spend_evidence(s, tmp_path):
    """Use F3b's durable ledger producer and committed, manager-owned usage receipt."""
    from test_spend_evidence import receipt

    from oversight.agents import LedgerFile
    from oversight.changes import RulesEvidence, SpendSnapshot

    ctx = s.service.ctx
    ledger = LedgerFile(tmp_path / "meta-ledger.json")
    await ledger.load()
    ctx.ports.spend_ledger = ledger

    def producer(inputs):
        state = ledger.state
        return RulesEvidence(
            replace(
                inputs,
                spend=SpendSnapshot(
                    spent_usd=state.spent_usd if state else None,
                    budget_usd=1,
                    unknown_costs=len(state.unknown) if state else None,
                    ledger_ok=ledger.loaded and state is not None,
                ),
            )
        )

    ctx.ports.rules_evidence = producer
    pin = await receipt(s)
    return ledger, pin
