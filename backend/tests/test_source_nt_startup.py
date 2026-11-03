"""Public startup ordering with injected NT filesystem seams; no native acceptance."""

import asyncio
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from live_helpers import live_settings, two_fakes
from workspace.test_source_nt_copy import fixture, stage

from oversight import app
from oversight.agents import ScriptedModel, TaskClients, parse_script
from oversight.workspace import InvalidPath, NtSource, SuiteRun, core, source_nt
from oversight.workspace._platform import preflight_placement
from oversight_contracts.commands import SaveFile, StartAgent
from oversight_contracts.events import CaseResult, SuiteResult


def native_selection(monkeypatch):
    # Inject only selector OS detection, leaving host runner/Path semantics intact.
    monkeypatch.setattr("oversight.workspace.source.sys", SimpleNamespace(platform="win32"))


def previous_runtime(tmp_path):
    sentinel = tmp_path / ".runtime/workspace/preserve"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"prior runtime")
    return sentinel


async def selected(tmp_path, monkeypatch, **settings):
    root = tmp_path / "source"
    root.mkdir()
    config = two_fakes(tmp_path, workspace_source="C:/private/source", **settings)
    own = NtSource(root, config.runtime_dir, "C:\\private\\source", "C:\\private\\runtime")
    await fixture(own)
    native_selection(monkeypatch)
    prepare = source_nt.prepare_source

    def prepared(raw, runtime, ide, venv):
        return prepare(
            raw,
            own.runtime_path,
            "C:/ide",
            venv,
            preflight=lambda _: None,
            inspect=lambda _: None,
            bind=lambda path: own.runtime if path == own.runtime_path else own.root,
        )

    monkeypatch.setattr(source_nt, "prepare_source", prepared)
    live = app.create_app(config).state.live

    async def staging():
        assert live.workspace._lock_fd is not None
        return await stage(own, live.workspace._lock_fd)

    monkeypatch.setattr(live.workspace, "stage_own_source", staging)
    return live, own


@pytest.mark.parametrize(
    "bad",
    [
        r"\\host\share",
        r"\\?\C:\project",
        r"\\.\C:\project",
        "relative",
        "C:relative",
        "C:/a/../b",
        "C:/a:s",
        "C:/GIT~1",
        "C:/a.",
        "C:/a ",
        "C:/CON",
        "C:/a//b",
        "C:/a?b",
    ],
)
def test_lexical_checks_precede_all_filesystem_and_construction(tmp_path, monkeypatch, bad):
    previous = previous_runtime(tmp_path)
    native_selection(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("lexical refusal reached filesystem or constructor")

    monkeypatch.setattr(app, "Workspace", forbidden)
    monkeypatch.setattr(app, "EventStore", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(source_nt, "preflight_placement", forbidden)
    with pytest.raises((OSError, InvalidPath, ValueError)):
        app.create_app(live_settings(tmp_path, workspace_source=bad))
    assert previous.read_bytes() == b"prior runtime"


@pytest.mark.parametrize("placement", ["runtime", "ide", "venv"])
def test_every_placement_lexically_validated_before_io(tmp_path, monkeypatch, placement):
    previous = previous_runtime(tmp_path)
    native_selection(monkeypatch)
    real_prepare = source_nt.prepare_source

    def forbidden(*args, **kwargs):
        pytest.fail("invalid placement reached I/O, constructors or source Git")

    def prepare(raw, runtime, ide, venv):
        paths = dict(source=raw, runtime="C:/runtime", ide="C:/ide", venv=venv)
        paths[placement] = "C:/unsafe/../placement"
        return real_prepare(
            paths["source"],
            paths["runtime"],
            paths["ide"],
            paths["venv"],
            preflight=forbidden,
            bind=forbidden,
        )

    monkeypatch.setattr(source_nt, "prepare_source", prepare)
    monkeypatch.setattr(app, "Workspace", forbidden)
    monkeypatch.setattr(app, "EventStore", forbidden)
    monkeypatch.setattr(source_nt, "run_process", forbidden)
    with pytest.raises((OSError, InvalidPath), match="segments are not allowed"):
        app.create_app(live_settings(tmp_path, workspace_source="C:/source"))
    assert previous.read_bytes() == b"prior runtime"


@pytest.mark.parametrize("source", ["C:/runtime", "C:/runtime/inside", "C:/"])
def test_public_overlap_precedes_io_and_construction(tmp_path, monkeypatch, source):
    previous = previous_runtime(tmp_path)
    native_selection(monkeypatch)
    real_prepare = source_nt.prepare_source

    def forbidden(*args, **kwargs):
        pytest.fail("overlap reached I/O or construction")

    def prepare(raw, runtime, ide, venv):
        return real_prepare(raw, "C:/runtime", "D:/ide", venv, preflight=forbidden)

    monkeypatch.setattr(source_nt, "prepare_source", prepare)
    monkeypatch.setattr(app, "Workspace", forbidden)
    monkeypatch.setattr(app, "EventStore", forbidden)
    monkeypatch.setattr(source_nt, "run_process", forbidden)
    with pytest.raises(OSError, match="overlap"):
        app.create_app(live_settings(tmp_path, workspace_source=source))
    assert previous.read_bytes() == b"prior runtime"


@pytest.mark.parametrize("placement", ["ide", "runtime", "source", "venv"])
def test_all_placements_precede_constructors(tmp_path, monkeypatch, placement):
    previous = previous_runtime(tmp_path)
    paths = dict(ide=r"C:\ide", runtime=r"C:\runtime", source=r"C:\source", venv=r"C:\source\.venv")
    called = []

    def preflight(path):
        called.append(path)
        if path == paths[placement]:
            raise OSError("placement refused")

    real_prepare = source_nt.prepare_source

    def prepare(raw, runtime, ide, venv):
        return real_prepare(
            *[paths[k] for k in ("source", "runtime", "ide", "venv")],
            preflight=preflight,
            inspect=lambda _: None,
        )

    native_selection(monkeypatch)
    monkeypatch.setattr(source_nt, "prepare_source", prepare)
    monkeypatch.setattr(app, "Workspace", lambda *a, **kw: pytest.fail("constructed workspace"))
    monkeypatch.setattr(app, "EventStore", lambda *a, **kw: pytest.fail("constructed store"))
    with pytest.raises(OSError, match="placement refused"):
        app.LiveRun(live_settings(tmp_path, workspace_source=paths["source"]))
    assert called[-1] == paths[placement]
    assert previous.read_bytes() == b"prior runtime"


@pytest.mark.parametrize("fault", ["network", "ReFS", "reparse"])
def test_public_placement_refuses_unsafe_volume_or_entry(tmp_path, monkeypatch, fault):
    native_selection(monkeypatch)
    real_prepare = source_nt.prepare_source
    previous = previous_runtime(tmp_path)

    def preflight(path):
        if path != r"C:\source":
            return
        volume = (
            4 if fault == "network" else 3,
            "ReFS" if fault == "ReFS" else "NTFS",
            r"\Device\HarddiskVolume1",
        )
        preflight_placement(
            path,
            nt=True,
            query=lambda _: volume,
            synced=[],
            lstat=lambda _: cast(
                os.stat_result, SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            ),
        )

    def prepare(raw, runtime, ide, venv):
        return real_prepare(
            raw, "C:/runtime", "C:/ide", venv, preflight=preflight, inspect=lambda _: None
        )

    def forbidden(*args, **kwargs):
        pytest.fail("unsafe placement reached constructors or source Git")

    monkeypatch.setattr(source_nt, "prepare_source", prepare)
    monkeypatch.setattr(app, "Workspace", forbidden)
    monkeypatch.setattr(app, "EventStore", forbidden)
    monkeypatch.setattr(source_nt, "run_process", forbidden)
    with pytest.raises(OSError, match=r"fixed local NTFS|reparse"):
        app.create_app(live_settings(tmp_path, workspace_source="C:/source"))
    assert previous.read_bytes() == b"prior runtime"


@pytest.mark.anyio
async def test_public_selected_content_refusal_preserves_previous(tmp_path, monkeypatch):
    live, own = await selected(tmp_path, monkeypatch)
    # Inventory is real; a selected linked file must refuse before reset or raw copying.
    (own.root / "new.py").unlink()
    (own.root / "new.py").symlink_to(own.root / "a.py")
    previous = own.runtime / "workspace/preserve"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"prior runtime")
    original = (own.root / "a.py").read_bytes()
    reset = AsyncMock(side_effect=AssertionError("destruction after unsafe inventory"))
    monkeypatch.setattr(live.workspace, "reset", reset)
    with pytest.raises((OSError, InvalidPath), match=r"linked|regular|reparse"):
        await live._reset_workspace()
    reset.assert_not_awaited()
    assert previous.read_bytes() == b"prior runtime"
    assert (own.root / "a.py").read_bytes() == original


@pytest.mark.anyio
@pytest.mark.parametrize(
    "refusal", ["lock/job", "prior", "isolation", "trust", "stage", "reset", None]
)
async def test_startup_order_and_refusal_preserves_previous(tmp_path, monkeypatch, refusal):
    live, own = await selected(tmp_path, monkeypatch)
    workspace = live.workspace
    own.runtime.mkdir()
    previous = own.runtime / "workspace"
    previous.mkdir()
    (previous / "preserve").write_bytes(b"prior runtime")
    calls = []

    def step(name):
        async def call(*args, **kwargs):
            calls.append(name)
            if name == refusal:
                raise OSError(name)
            if name == "stage":
                return object()
            if name == "reset":
                assert "_nt_stage" in kwargs
                return "base"

        return call

    for name, target in (
        ("lock/job", "acquire_runtime"),
        ("prior", "guard_previous_runtime"),
        ("stage", "stage_own_source"),
        ("reset", "reset"),
    ):
        monkeypatch.setattr(workspace, target, step(name))
    monkeypatch.setattr(workspace, "check_isolation", step("isolation"))
    monkeypatch.setattr(live, "_disclose_source", step("trust"))
    # Staging's type/metadata handoff is exercised with real staging in the next tests.
    monkeypatch.setattr(
        workspace,
        "stage_own_source",
        AsyncMock(side_effect=OSError("stage"))
        if refusal == "stage"
        else AsyncMock(
            return_value=SimpleNamespace(
                selection=SimpleNamespace(inventory=SimpleNamespace(skipped_roots=2))
            )
        ),
    )
    staging = workspace.stage_own_source

    async def record_stage():
        calls.append("stage")
        return await staging()

    monkeypatch.setattr(workspace, "stage_own_source", record_stage)
    if refusal:
        with pytest.raises(OSError, match=refusal):
            await live._reset_workspace()
    else:
        assert await live._reset_workspace() == "base"
    order = ["lock/job", "prior", "isolation", "trust", "stage", "reset"]
    assert calls == order[: order.index(refusal) + 1] if refusal else calls == order
    assert (previous / "preserve").read_bytes() == b"prior runtime"


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["", "boltons-d31d821"])
async def test_selector_change_cannot_evade_previous_guard(tmp_path, monkeypatch, source):
    live = app.LiveRun(live_settings(tmp_path, workspace_source=source))
    monkeypatch.setattr(
        core,
        "guard_previous",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("preserve own runtime")),
    )
    monkeypatch.setattr(
        app, "validate_source", AsyncMock(side_effect=AssertionError("profile Git"))
    )
    monkeypatch.setattr(live.workspace, "reset", AsyncMock(side_effect=AssertionError("deletion")))
    with pytest.raises(OSError, match="preserve own"):
        await live._reset_workspace()
    assert live.workspace._lock_fd is None


@pytest.mark.anyio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_profile_cleanup_failure_keeps_runtime_lock(tmp_path, monkeypatch, cancelled):
    from oversight.workspace import IntegrityError, WorkspaceBusy
    from oversight.workspace.runner import NtCleanupError

    live = app.LiveRun(live_settings(tmp_path, workspace_source="boltons-d31d821"))
    cleanup = NtCleanupError("profile Job still alive")
    pending = (
        asyncio.CancelledError("profile stopped") if cancelled else ValueError("profile failed")
    )

    async def validate(*args, spawn):
        # validate_source wraps an OSError; run_process chains cancellation to its failure.
        assert spawn == live.workspace.startup_git
        raise pending from cleanup

    monkeypatch.setattr(app, "validate_source", validate)
    reset = AsyncMock(side_effect=AssertionError("reset after unverified cleanup"))
    monkeypatch.setattr(live.workspace, "reset", reset)
    try:
        with pytest.raises(type(pending)) as caught:
            await live._reset_workspace()
        assert caught.value is pending and caught.value.__cause__ is cleanup
        reset.assert_not_awaited()
        assert live.workspace.integrity.main_suspect and live.workspace.integrity.rules_suspended
        assert live.workspace._lock_fd is not None
        other = core.Workspace(
            live.workspace.runtime_root, tmp_path / "template", pytest_pin="9.1.1"
        )
        with pytest.raises(WorkspaceBusy):
            await other.acquire_runtime()
        with pytest.raises(IntegrityError):
            live.workspace.integrity.before_git()
    finally:
        live.workspace._cleanup_unverified = False
        live.workspace.release_runtime()


@pytest.mark.anyio
@pytest.mark.parametrize("limit,wall", [(75, 600), (300, 1200)])
async def test_real_staging_and_stubbed_two_agent_suite_forwarding(
    tmp_path, monkeypatch, caplog, limit, wall
):
    # This suite stub proves forwarding/composition only; native fixtures prove admission.
    live, own = await selected(tmp_path, monkeypatch, test_timeout_s=limit)
    before = {
        str(p.relative_to(own.root)): p.read_bytes() for p in own.root.rglob("*") if p.is_file()
    }
    monkeypatch.setattr(
        live.workspace, "ensure_demo_venv", AsyncMock(side_effect=AssertionError("builder"))
    )
    # Missing venv is unavailable, and never a reason to lose graph/read/edit access.
    try:
        await live.startup()
        assert live._venv is None
        assert live._environment_unavailable is not None
        assert live.store.graph()[1] is not None
        readable = await live.get_file("new.py", None)
        assert readable.content == "new\r\n"
        await live.save_file(
            SaveFile(path="new.py", content="new = 2\r\n", base_hash=readable.hash)
        )
        assert live._limits.test_timeout_s == limit and live._limits.max_wall_clock_s == wall
        assert live._manager is not None and live._manager._revert is not None
        assert live._manager._revert.service.test_timeout_s == limit
        monkeypatch.setenv("OVERSIGHT_TEST_TIMEOUT_S", "999")
        object.__setattr__(live._settings, "test_timeout_s", 1)
        (own.root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        assert live._limits.test_timeout_s == limit
        script = parse_script(
            '{"task":"Edit the copy","turns":['
            '{"tool_calls":[{"name":"read_file","arguments":{"path":"new.py"}}]},'
            '{"tool_calls":[{"name":"edit_file","arguments":{"path":"new.py",'
            '"old_text":"new = 2\\r\\n","new_text":"new = 3\\r\\n"}}]},'
            '{"tool_calls":[{"name":"run_tests","arguments":{}}]},'
            '{"tool_calls":[{"name":"finish","arguments":{"summary":"Edit verified."}}]}]}'
        )
        live._client_factory = TaskClients(lambda profile, slot: ScriptedModel(script, profile))
        calls = []

        async def suite(**kwargs):
            calls.append((kwargs["tree_root"], kwargs["timeout_s"]))
            assert kwargs["python"] == own.root / ".venv/Scripts/python.exe"
            assert kwargs["_own_prepare"] is not None
            return SuiteRun(
                SuiteResult(
                    outcome="passed", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]
                ),
                "",
                cases=(CaseResult(test_id="test_copy.py::test_value", outcome="passed"),),
                problems=(),
            )

        monkeypatch.setattr("oversight.workspace.suite_guard.run_suite", suite)
        for slot in ("A", "B"):
            await live.start(StartAgent(task="Inspect this copy", slot=slot))
        assert len(live._agents) == 2
        assert all(agent.task.limits is live._limits for agent in live._agents.values())
        await asyncio.gather(*list(live._runs.values()))
        proposals = [
            e.payload for e in live.store.committed_events() if e.type == "change.proposed"
        ]
        assert len(proposals) == 2
        nested = await live.get_file("nested/code.py", None)
        await live.save_file(
            SaveFile(path=nested.path, content="value = 2\r\n", base_hash=nested.hash)
        )
        proposal = proposals[0]
        await live._manager.approve(
            proposal.change_id, None, revision=proposal.revision, head_commit=proposal.head_commit
        )
        assert len(calls) == 5 and calls[-1][0] == live.workspace.main.root
        assert all(timeout == limit for _, timeout in calls)
        assert (
            caplog.messages.count(
                f"Test limit: {limit} s. Agent time limit: {wall} s per round. "
                "These settings stay fixed for this run."
            )
            == 1
        )
        before.pop("pyproject.toml", None)
        after = {
            str(p.relative_to(own.root)): p.read_bytes()
            for p in own.root.rglob("*")
            if p.is_file() and p.name != "pyproject.toml"
        }
        assert before == after
        assert (own.runtime / "own-trust").is_file()
    finally:
        await live.shutdown()


@pytest.mark.anyio
async def test_staged_reset_cancellation_cleans_only_before_destruction(tmp_path, monkeypatch):
    live, own = await selected(tmp_path, monkeypatch)
    await live.workspace.acquire_runtime()
    staged = await live.workspace.stage_own_source()
    foreign = own.runtime / "source-stage-foreign"
    foreign.mkdir()
    monkeypatch.setattr(
        core, "guard_previous", lambda *a, **kw: (_ for _ in ()).throw(asyncio.CancelledError())
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await live.workspace.reset(_nt_stage=staged)
        assert not staged.root.exists() and foreign.exists()
    finally:
        live.workspace.release_runtime()


@pytest.mark.anyio
@pytest.mark.parametrize("stop", ["inventory", "validate", "stage", None])
async def test_workspace_stage_adapter_order_and_refusals(tmp_path, monkeypatch, stop):
    live, own = await selected(tmp_path, monkeypatch)
    calls = []
    previous = own.runtime / "workspace/preserve"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"prior runtime")
    original = (own.root / "a.py").read_bytes()
    monkeypatch.setattr(
        live.workspace,
        "stage_own_source",
        lambda: core.Workspace.stage_own_source(live.workspace),
    )
    reset = AsyncMock(side_effect=AssertionError("destruction before validated staging"))
    monkeypatch.setattr(live.workspace, "reset", reset)

    def action(name):
        def call(*args, **kwargs):
            calls.append(name)
            assert live.workspace._lock_fd is not None
            if stop == name:
                raise OSError(name)
            return name

        return call

    monkeypatch.setattr(core, "executable", lambda _: "C:/Git/git.exe")
    monkeypatch.setattr(core, "inventory", AsyncMock(side_effect=action("inventory")))
    monkeypatch.setattr(core, "validate_selection", action("validate"))
    monkeypatch.setattr(core, "stage_source", AsyncMock(side_effect=action("stage")))
    await live.workspace.acquire_runtime()
    try:
        if stop:
            with pytest.raises(OSError, match=stop):
                await live._reset_workspace()
        else:
            assert await core.Workspace.stage_own_source(live.workspace) == "stage"
        sequence = ["inventory", "validate", "stage"]
        assert calls == sequence[: sequence.index(stop) + 1] if stop else calls == sequence
        reset.assert_not_awaited()
        assert previous.read_bytes() == b"prior runtime"
        assert (own.root / "a.py").read_bytes() == original
    finally:
        live.workspace.release_runtime()
