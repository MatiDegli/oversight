"""Wiring gates distinguish static fixtures from deployed namespace proofs."""

import asyncio
from dataclasses import replace

import pytest

from oversight.workspace._reviewed_preview import prepare_reviewed, validate_storage
from oversight.workspace.preview_preparation import PreparationInputs, prepare_preview

from .test_preview_preparation import inputs as inputs

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("field", ["preview_directory", "preparation_storage"])
async def test_reviewed_storage_exclusions_precede_preparation(
    inputs: PreparationInputs, field: str
):
    original = inputs.excluded_roots[0]
    with pytest.raises(ValueError, match="excluded"):
        await prepare_reviewed(replace(inputs, **{field: original / "previews/example"}))
    assert not (original / "previews").exists()


async def test_reviewed_preparation_off_loop_and_settled(inputs, monkeypatch):
    import threading

    from oversight.workspace import _reviewed_preview as wiring

    started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    original = prepare_preview

    async def prepare(value):
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        try:
            return await original(value)
        finally:
            finished.set()

    monkeypatch.setattr(wiring, "prepare_preview", prepare)
    task = asyncio.create_task(prepare_reviewed(inputs))
    async with asyncio.timeout(5):
        await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_reviewed_exclusion_aliases(inputs, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(inputs.excluded_roots[0], target_is_directory=True)
    with pytest.raises(ValueError, match="excluded"):
        validate_storage(replace(inputs, excluded_roots=(alias,), tree=inputs.excluded_roots[0]))


async def test_reviewed_launch_durable_before_exec(tmp_path):
    from oversight.workspace.preview_boundary import launch_boundary
    from oversight.workspace.preview_registry import PreviewRegistry, identity_data

    from .preview_boundary_support import config, prepared

    roots = await asyncio.to_thread(prepared, tmp_path / "boundary")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    registry = PreviewRegistry(runtime)
    registry.write(dict(version=1, runtime=str(runtime), phase="intent"))
    release = asyncio.Event()
    confirmed = asyncio.Event()
    marker = roots.tree / "EXECUTED"

    async def persist(handle):
        assert not marker.exists()
        assert set(handle.processes) == {"outer", "setup", "init"}
        assert set(handle.namespace_fds) == {"u0", "n0", "u1", "pid", "mount"}
        registry.update(
            phase="confirmed",
            processes={k: identity_data(v.pid) for k, v in handle.processes.items()},
        )
        confirmed.set()
        await release.wait()
        assert (data := registry.read()) is not None and data["phase"] == "confirmed"
        assert not marker.exists()
        registry.update(phase="executing")

    task = asyncio.create_task(
        launch_boundary(
            config("from pathlib import Path; Path('/T/EXECUTED').write_text('yes')"),
            roots,
            before_exec=persist,
        )
    )
    try:
        async with asyncio.timeout(20):
            await confirmed.wait()
        assert (data := registry.read()) is not None and data["phase"] == "confirmed"
        assert not marker.exists()
        release.set()
        handle = await task
        await handle.wait()
        assert handle.returncode == 0, handle.stderr
        assert marker.read_text() == "yes"
        assert (data := registry.read()) is not None and data["phase"] == "executing"
        assert handle.verified_dead
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_reviewed_persistence_failure_keeps_execution_closed(tmp_path):
    from oversight.workspace.preview_boundary import BoundaryUnavailable, launch_boundary

    from .preview_boundary_support import config, prepared

    roots = await asyncio.to_thread(prepared, tmp_path / "boundary")
    owned = []

    async def refuse(handle):
        owned.append(handle)
        raise OSError("durability failed")

    with pytest.raises(BoundaryUnavailable, match="durability failed"):
        await launch_boundary(
            config("from pathlib import Path; Path('/T/EXECUTED').touch()"),
            roots,
            before_exec=refuse,
        )
    assert owned[0].verified_dead
    assert not (roots.tree / "EXECUTED").exists()
