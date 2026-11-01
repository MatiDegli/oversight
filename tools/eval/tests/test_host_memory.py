"""The eval server harness must not depend on the host's memory pressure."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from live_helpers import (  # pyright: ignore[reportMissingImports]
    DEADLINE_S,
    GatedModel,
    live_server,
    live_settings,
)


@pytest.mark.anyio
async def test_full_host_memory_does_not_hold_eval_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.read_text
    reads: list[Path] = []

    def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == Path("/proc/meminfo"):
            reads.append(path)
            return "MemTotal: 100 kB\nMemAvailable: 0 kB\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    model = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as server,
    ):
        response = await server.client.post("/agents", json={"task": "Work.", "model": "fake"})
        assert response.status_code == 201, response.text
        await model.entered.wait()
        assert not reads
