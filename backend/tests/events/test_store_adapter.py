"""The shared event adapter keeps initialization out of publication and preserves admission."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pydantic
import pytest

from oversight.events import EventRejected, EventStore, read_store
from oversight.events import store as store_module
from oversight_contracts.events import ChangeResolved, ErrorReport, ServerEvent

pytestmark = pytest.mark.anyio
RUN = "run-20261027-140312"


async def test_server_event_adapter_is_built_once_before_concurrent_publications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructor = Mock(wraps=pydantic.TypeAdapter)
    monkeypatch.setattr(pydantic, "TypeAdapter", constructor)
    # Execute an isolated import so the count includes initialization without replacing the
    # classes (especially exceptions) that other tests have imported from the real module.
    name = "oversight.events._store_adapter_test"
    spec = importlib.util.spec_from_file_location(name, store_module.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    constructor.assert_called_once_with(ServerEvent)
    assert module._SERVER_EVENT_ADAPTER.pydantic_complete

    payload = ErrorReport(
        source="server", message="test", agent_id=None, change_id=None, fatal=False
    )
    async with (
        module.EventStore(tmp_path / "first.sqlite", RUN) as first,
        module.EventStore(tmp_path / "second.sqlite", RUN) as second,
    ):
        rows = await asyncio.gather(
            *(store.publish(payload) for store in (first, second) for _ in range(50))
        )
        assert len(rows) == 100
        assert first.last_seq == second.last_seq == 50
    constructor.assert_called_once_with(ServerEvent)


async def test_live_resolution_is_still_rejected_at_commit_with_the_same_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    historical = ChangeResolved(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        decision="rejected",
        reason=None,
        comment=None,
        merge=None,
        merge_commit=None,
        conflict=None,
    )
    async with EventStore(tmp_path / "live.sqlite", RUN) as store:
        put = AsyncMock(wraps=store._queue.put)  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(store._queue, "put", put)  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(EventRejected, match="live resolutions require explicit provenance"):
            await store.publish(historical)
        # JSON validation admits this historical payload; live validation rejects it only
        # after enqueueing, before any sequence is committed.
        put.assert_awaited_once()
        assert store.last_seq == 0
        # Context on the shared adapter must not leak into another store's replay validation.
        async with EventStore(tmp_path / "replay.sqlite", RUN, mode="replay") as replay:
            assert (await replay.publish(historical)).seq == 1
        valid = historical.model_copy(update={"decided_by": "person"})
        assert (await store.publish(valid)).seq == 1
    assert len(read_store(store.path)) == 1
