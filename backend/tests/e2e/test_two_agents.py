"""Two agents on the live app, end to end: their calls interleaved in one log, back-to-back
approvals, a failing combination of two edits that are each green alone, and a shutdown with both
mid-call."""

import asyncio
from pathlib import Path

import pytest
from live_helpers import (
    DEADLINE_S,
    BySlot,
    GatedModel,
    Scripts,
    Stepped,
    error_code,
    is_type,
    live_server,
    script_json,
    start,
    two_fakes,
)

from oversight.agents import parse_script

pytestmark = pytest.mark.anyio


async def test_two_agents_interleave_in_one_log(tmp_path: Path) -> None:
    listing = {"tool_calls": [{"name": "list_files", "arguments": {}}]}
    three = parse_script(script_json("Look around.", [listing, listing, listing]))
    models = {"A": Stepped(three), "B": Stepped(three)}
    clients = BySlot(lambda _profile, slot: models[slot])
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), clients) as s:
        assert (await start(s)).status_code == 201
        assert (await start(s)).status_code == 201
        # One call each in turn, each waited for on its own `agent.step`: A1 B1 A2 B2 A3 B3.
        for step in (1, 2, 3):
            for slot, agent in (("A", "agent-1"), ("B", "agent-2")):
                models[slot].permits.release()
                await s.until(is_type("agent.step", agent_id=agent, step=step))
        events = s.events()  # each one validated against `ServerEvent` by the helper
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    steps = [(e.payload.agent_id, e.payload.step) for e in events if e.type == "agent.step"]
    assert steps == [(agent, step) for step in (1, 2, 3) for agent in ("agent-1", "agent-2")]
    statuses = {(e.payload.agent_id, e.payload.slot) for e in events if e.type == "agent.status"}
    assert statuses == {("agent-1", "A"), ("agent-2", "B")}


async def test_back_to_back_approvals_chain_their_deltas(tmp_path: Path) -> None:
    # A and B edit different files. Both approvals are sent at once: the second waits for main's
    # lock, merges on the moved main and starts its delta where the first one ended.
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path)) as s:
        assert (await start(s, slot="A")).status_code == 201
        assert (await start(s, slot="B")).status_code == 201
        for agent in ("agent-1", "agent-2"):
            await s.until(is_type("agent.status", agent_id=agent, state="ready_for_review"))
        first = await s.client.post("/changes/change-1/approve")
        second = await s.client.post("/changes/change-2/approve")
        assert (first.status_code, second.status_code) == (202, 202)
        for agent in ("agent-1", "agent-2"):
            await s.until(is_type("agent.status", agent_id=agent, state="done"))
        events = s.events()
    merges = [(e.payload.change_id, e.payload.merge) for e in events if e.type == "change.resolved"]
    assert merges == [("change-1", "merged"), ("change-2", "merged")]
    deltas = [e.payload for e in events if e.type == "graph.delta"]
    assert len(deltas) == 2 and deltas[1].from_version == deltas[0].to_version


TOTAL_TEMPLATE = {
    "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = ['tests']\npythonpath = ['.']\n",
    "shop/__init__.py": "",
    "shop/pricing.py": "def price() -> int:\n    return 1\n",
    "shop/stock.py": "def count() -> int:\n    return 1\n",
    # Alone, each edit leaves the total at 3; both together make it 4.
    "tests/test_total.py": (
        "from shop.pricing import price\nfrom shop.stock import count\n\n\n"
        "def test_total_stays_small() -> None:\n    assert price() + count() < 4\n"
    ),
}


def raise_to_two(path: str) -> str:
    """A script that reads `path`, turns its `return 1` into `return 2`, tests, and finishes."""
    calls = [
        {"name": "read_file", "arguments": {"path": path}},
        {
            "name": "edit_file",
            "arguments": {"path": path, "old_text": "return 1", "new_text": "return 2"},
        },
        {"name": "run_tests", "arguments": {}},
        {"name": "finish", "arguments": {"summary": f"Raised {path}."}},
    ]
    return script_json("Raise it.", [{"tool_calls": [call]} for call in calls])


async def test_a_then_b_with_a_failing_combination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each edit passes the suite alone. Approving A moves main; B's merge, staged on that main, runs
    # the suite again and fails: B conflicts, main stays at A's merge and B keeps its slot.
    template = tmp_path / "template"
    for name, text in TOTAL_TEMPLATE.items():
        (template / name).parent.mkdir(parents=True, exist_ok=True)
        (template / name).write_text(text)
    monkeypatch.setattr("oversight.app.TEMPLATE", template)
    scripts = Scripts([raise_to_two("shop/pricing.py"), raise_to_two("shop/stock.py")])
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), scripts) as s:
        assert (await start(s, slot="A")).status_code == 201
        assert (await start(s, slot="B")).status_code == 201
        for agent in ("agent-1", "agent-2"):
            await s.until(is_type("agent.status", agent_id=agent, state="ready_for_review"))
        assert (await s.client.post("/changes/change-1/approve")).status_code == 202
        await s.until(is_type("change.resolved", change_id="change-1"))
        workspace = s.live.workspace

        async def main_state() -> tuple[str, str]:
            async with workspace.locks(main=True):
                head = await workspace.git_ok(workspace.main, "rev-parse", "HEAD")
                status = await workspace.git_ok(workspace.main, "status", "--porcelain")
            return head.strip(), status

        after_a = await main_state()
        assert after_a[1] == ""  # clean
        assert (await s.client.post("/changes/change-2/approve")).status_code == 202
        await s.until(is_type("change.resolved", change_id="change-2"))
        assert await main_state() == after_a  # B's merge was aborted: same head, still clean
        events = s.events()
        refused = await start(s, slot="B")
        main_files = {
            path: (workspace.main.root / path).read_text()
            for path in ("shop/pricing.py", "shop/stock.py")
        }
    proposed = [e.payload for e in events if e.type == "change.proposed"]
    assert [(p.change_id, p.tests.outcome) for p in proposed] == [
        ("change-1", "passed"),
        ("change-2", "passed"),
    ]
    resolved = [e.payload for e in events if e.type == "change.resolved"]
    assert [(r.change_id, r.decision, r.merge) for r in resolved] == [
        ("change-1", "approved", "merged"),
        ("change-2", "approved", "conflict"),
    ]
    assert resolved[0].merge_commit == after_a[0]
    conflict = resolved[1].conflict
    assert conflict is not None and conflict.tests is not None
    assert (conflict.kind, conflict.paths, conflict.tests.outcome) == ("tests", [], "failed")
    assert conflict.tests.failed_tests == ["tests/test_total.py::test_total_stays_small"]
    assert len([e for e in events if e.type == "graph.delta"]) == 1  # A's merge, not B's
    assert main_files["shop/pricing.py"] == "def price() -> int:\n    return 2\n"
    assert main_files["shop/stock.py"] == "def count() -> int:\n    return 1\n"
    # A's slot is free, B's is still held: it can ask the agent again or be rejected.
    assert s.live._slots == {"A": None, "B": "agent-2"}  # pyright: ignore[reportPrivateUsage]
    assert error_code(refused) == (409, "slot_busy")


async def test_shutdown_mid_two_runs_ends_both(tmp_path: Path) -> None:
    gated = {"A": GatedModel(), "B": GatedModel()}
    clients = BySlot(lambda _profile, slot: gated[slot])
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), clients) as s:
        created = [(await start(s)).json(), (await start(s)).json()]
        assert [c["slot"] for c in created] == ["A", "B"]
        for model in gated.values():
            await model.entered.wait()
        await s.stop()
        assert all(model.cancelled.is_set() for model in gated.values())
        assert s.live._runs == {}  # pyright: ignore[reportPrivateUsage]
        # Both changes were ended before the store closed: none is left `working`, which no
        # decision can take, so the manager's drain had nothing to wait for.
        manager = s.live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        for agent in created:
            assert manager._agents[agent["agent_id"]].state == "unproposed"  # pyright: ignore[reportPrivateUsage]
