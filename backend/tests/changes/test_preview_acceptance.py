"""Reuse landed authority/refusal tests after real C launch; only drain failure is injected."""

import asyncio
import os
import sys
from pathlib import Path

import changes_helpers
import pytest
import test_preview_mutations as landed
from test_grant_rules import granted as granted
from test_rules_transaction import reserved as reserved
from workspace.preview_acceptance_support import (
    dead,
    preflight,
)
from workspace.preview_acceptance_support import (
    no_kernel_startup_probe as no_kernel_startup_probe,
)

from oversight.changes import WrongState
from oversight.settings import REPO_ROOT
from oversight.workspace import IntegrityError, PreviewError, PreviewProfile, core, run_process
from oversight.workspace import preview_barrier as barriers
from oversight.workspace.preview_process import PreviewProcess
from oversight.workspace.preview_registry import registered_identity
from oversight.workspace.snapshot import finish_snapshot

# isort: split
sys.path.insert(0, str(REPO_ROOT))
from tools.preview_acceptance_checks import free_port, namespace_members

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("OVERSIGHT_ISOLATION_KERNEL_TESTS") != "1",
        reason="optional real-C authority acceptance",
    ),
]


@pytest.fixture
async def h(tmp_path, monkeypatch):
    await preflight(tmp_path / "preflight")
    # The landed authority harness retains shop.mod and its graph/test semantics. Add the
    # repository's own shop adapter inputs so that the actual trusted adapter can import them.
    extra = {
        str(path.relative_to(REPO_ROOT / "demo-repo")): path.read_text()
        for path in (REPO_ROOT / "demo-repo/shop").glob("*.py")
        if path.name != "__init__.py"
    }
    monkeypatch.setattr(changes_helpers, "TEMPLATE", changes_helpers.TEMPLATE | extra)
    harness = await changes_helpers.start_harness(tmp_path)
    await harness.workspace.check_isolation()
    assert harness.workspace.isolation.available
    processes = wire_real_preview(harness, monkeypatch)
    try:
        yield harness
    finally:
        for process, data in processes:
            # Testing an uncertain proof must not leave a live test application. This direct
            # cleanup verifies only exact registered ownership; quarantine/locks remain latched.
            await PreviewProcess.stop(process)
            await dead(data)
        manager = harness.workspace.previews
        if manager and manager._watcher:
            manager._watcher.cancel()
            await asyncio.gather(manager._watcher, return_exceptions=True)
        await harness.close()


def wire_real_preview(harness, monkeypatch):
    processes = []

    class RealFaultProcess(PreviewProcess):
        stop_failure = False

        async def launch(self, listener, profile):
            await super().launch(listener, profile)
            data = self.registry.read()
            assert data is not None and data["phase"] == "executing"
            processes.append((self, data))

        async def stop(self):
            if self.stop_failure:
                raise PreviewError("Injected unavailable death proof after real C launch.")
            await super().stop()

    original_spawn = run_process

    def finish(snapshot, state):
        for process, data in processes:
            assert process.dead and namespace_members(data["namespaces"]["pid"]) == []
        finish_snapshot(snapshot, state)

    async def spawn(argv, **kwargs):
        if processes:
            assert all(process.dead for process, _ in processes)
            manager = harness.workspace.previews
            assert manager is not None
            owner = manager.barrier.owner
            assert owner is None or owner.snapshot_finished
        return await original_spawn(argv, **kwargs)

    monkeypatch.setattr(barriers, "finish_snapshot", finish)
    monkeypatch.setattr(core, "run_process", spawn)
    monkeypatch.setattr(landed, "FakeProcess", RealFaultProcess)
    monkeypatch.setattr(
        landed,
        "profile",
        lambda: PreviewProfile(
            adapter=REPO_ROOT / "backend/src/oversight/preview_adapter.py",
            interpreter=Path(sys.executable),
            ports=(free_port(), free_port()),
            origins=("http://127.0.0.1:8710",),
            trusted_demo=True,
        ),
    )
    return processes


@pytest.mark.parametrize(
    "operation",
    ["approve", "reject", "stop", "revision", "user_edit", "agent_edit", "remove", "disable"],
)
async def test_failed_real_drain_refuses_every_mutation_before_effect(h, operation):
    await landed.test_each_mutation_refuses_before_first_effect(h, operation)
    previews = h.workspace.previews
    assert previews is not None and previews.has_owner and previews.registry.read() is not None
    assert h.workspace.runtime_owned and h.workspace.integrity.main_suspect
    data = previews.registry.read()
    assert data is not None and registered_identity(data["namespace"]).alive()
    _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", data["port"]), 2)
    writer.close()
    await writer.wait_closed()
    h.workspace.release_runtime()
    assert h.workspace.runtime_owned
    from decision_helpers import observed_target

    with pytest.raises((PreviewError, IntegrityError, WrongState)):
        await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert not h.manager._agents["agent-1"].merged
    main_ref = h.workspace.main.git_identity.common / "refs/heads/main"
    assert main_ref.read_text().strip() == h.base_commit
    calls = len(h.workspace.calls)
    with pytest.raises(IntegrityError):
        await h.workspace.main.head()
    # FaultyWorkspace records attempted calls, but the real runner must never execute Git.
    assert len(h.workspace.calls) <= calls + 1


@pytest.mark.parametrize("tamper", ["files", "metadata"])
async def test_real_contamination_preserves_candidate_and_refuses_person(h, tamper):
    await landed.test_contamination_blocks_person_before_automatic_reproposal_or_stage(h, tamper)


@pytest.mark.parametrize("contamination", [False, True, "metadata"])
async def test_real_preview_refuses_rules_authority(h, reserved, monkeypatch, contamination):
    await landed.test_rules_authority_refuses_cleanup_failure_or_contamination(
        h, reserved, monkeypatch, contamination
    )


@pytest.mark.parametrize("contamination", [False, True, "metadata"])
async def test_real_preview_refuses_standing_grant_authority(
    h, granted, monkeypatch, contamination
):
    await landed.test_standing_grant_authority_uses_same_barriers(
        h, granted, monkeypatch, contamination
    )


@pytest.mark.parametrize("operation", ["proposal", "revision"])
async def test_other_agent_git_waits_for_real_namespace_death_and_snapshot(h, operation):
    from changes_helpers import EDIT
    from decision_helpers import observed_target

    await h.run_agent(1, EDIT)
    await h.open(2)
    await h.edit(2, *EDIT)
    if operation == "revision":
        await h.finish(2)
    previews, process, sink, _ = await landed.attach(h)
    if operation == "proposal":
        await h.finish(2)
    else:
        await h.manager.request_changes(
            "change-2", "Please revise.", **observed_target(h.manager, "change-2")
        )
    await previews.barrier.finish_pending()
    assert process.dead and sink.records[-1].reason == "mutation"
    assert previews.registry.read() is None and not previews.has_owner
