"""Admission for one through six slots retains atomic reservations and per-slot scripts."""

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from decision_helpers import observed_body
from live_helpers import (
    DEADLINE_S,
    BySlot,
    GatedModel,
    error_code,
    is_type,
    live_server,
    live_settings,
    script_json,
    start,
)

from oversight.agents import FAKE_MODEL
from oversight.api import ApiError
from oversight.app import LiveRun
from oversight.changes import ChangeManager, WrongState
from oversight.review_config import ReviewerUnavailable, configured_reviewer
from oversight.settings import Settings
from oversight_contracts.commands import AgentCreated, StartAgent
from oversight_contracts.common import Slot

pytestmark = pytest.mark.anyio
LETTERS: tuple[Slot, ...] = ("A", "B", "C", "D", "E", "F")


def fake_settings(tmp_path: Path, maximum: int) -> Settings:
    """Each offered slot has a distinct script, edit and finish summary; fixtures stay intact."""
    paths: dict[str, Path] = {}
    for slot in LETTERS[:maximum]:
        path = tmp_path / f"{slot}.json"
        old = "def apply_coupon(total: int, code: str) -> int:\n"
        path.write_text(
            script_json(
                f"Document coupon for {slot}.",
                [
                    {
                        "tool_calls": [
                            {"name": "read_file", "arguments": {"path": "shop/pricing.py"}}
                        ]
                    },
                    {
                        "tool_calls": [
                            {
                                "name": "edit_file",
                                "arguments": {
                                    "path": "shop/pricing.py",
                                    "old_text": old,
                                    "new_text": old + f'    """Slot {slot} documents coupons."""\n',
                                },
                            }
                        ]
                    },
                    {"tool_calls": [{"name": "finish", "arguments": {"summary": f"Done {slot}."}}]},
                ],
            )
        )
        paths["fake_script" + (f"_{slot.lower()}" if slot != "A" else "")] = path
    return live_settings(tmp_path, max_agents=maximum, **paths)


@pytest.mark.parametrize("maximum", [4, 6])
async def test_first_free_order_own_scripts_and_run_started(tmp_path: Path, maximum: int) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(fake_settings(tmp_path, maximum)) as s:
        assert s.store.allow_fake_provider
        run = s.events()[0].payload
        assert [(entry.slot, entry.default_model) for entry in run.slots] == [
            (slot, "fake") for slot in LETTERS[:maximum]
        ]
        created = [(await start(s)).json() for _ in range(maximum)]
        assert [c["slot"] for c in created] == list(LETTERS[:maximum])
        for c in created:
            await s.until(is_type("agent.status", agent_id=c["agent_id"], state="ready_for_review"))
        proposals = [e.payload for e in s.events() if e.type == "change.proposed"]
        assert {p.summary for p in proposals} == {f"Done {slot}." for slot in LETTERS[:maximum]}
        updates = [e.payload for e in s.events() if e.type == "change.updated"]
        for c in created:
            assert any(
                u.agent_id == c["agent_id"]
                and f"Slot {c['slot']} documents coupons." in (u.file.modified or "")
                for u in updates
            )
        busy = await start(s)
        assert error_code(busy) == (409, "slot_busy")
        holders = ", ".join(c["agent_id"] for c in created)
        assert busy.json()["error"]["message"] == f"all {maximum} slots are taken ({holders})"


@pytest.mark.parametrize("maximum", [4, 6])
async def test_named_c_is_not_rerouted_when_busy(tmp_path: Path, maximum: int) -> None:
    clients = BySlot(lambda profile, _slot: GatedModel(profile=profile))
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(fake_settings(tmp_path, maximum), clients) as s,
    ):
        created = await start(s, slot="C")
        assert created.status_code == 201 and created.json()["slot"] == "C"
        busy = await start(s, slot="C")
        assert error_code(busy) == (409, "slot_busy")
        assert busy.json()["error"]["message"] == "slot C is taken (agent-1)"
        assert (await start(s)).json()["slot"] == "A"


@pytest.mark.parametrize("maximum", [1, 2, 4])
async def test_a_slot_beyond_capacity_is_invalid(tmp_path: Path, maximum: int) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(fake_settings(tmp_path, maximum)) as s:
        refused = await start(s, slot=LETTERS[maximum])
        assert error_code(refused) == (422, "invalid_request")
        assert refused.json()["error"]["message"] == f"slot {LETTERS[maximum]} is not offered"


@pytest.mark.parametrize("maximum", [4, 6])
async def test_race_for_n_minus_one_free_slots_then_reject_and_reuse(
    tmp_path: Path, maximum: int
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(fake_settings(tmp_path, maximum)) as s:
        # Hold the last slot, leaving N-1 free for N simultaneous starts.
        assert (await start(s, slot=LETTERS[maximum - 1])).status_code == 201
        assert s.live._venv is not None  # pyright: ignore[reportPrivateUsage]
        await s.live._venv  # pyright: ignore[reportPrivateUsage]
        release = asyncio.Event()

        async def build() -> None:
            await release.wait()

        s.live._venv = asyncio.create_task(build())  # pyright: ignore[reportPrivateUsage]
        loop = asyncio.get_running_loop()
        pending = [
            asyncio.Task(s.live.start(StartAgent(task="t")), loop=loop, eager_start=True)
            for _ in range(maximum)
        ]
        holders = s.live._slots  # pyright: ignore[reportPrivateUsage]
        assert list(holders.values()) == ["starting"] * (maximum - 1) + ["agent-1"]
        assert sum(t.done() for t in pending) == 1
        release.set()
        results = await asyncio.gather(*pending, return_exceptions=True)
        errors = [r for r in results if isinstance(r, ApiError)]
        assert len(errors) == 1 and errors[0].code == "slot_busy"
        assert errors[0].message == (
            f"all {maximum} slots are taken ("
            + ", ".join(["starting"] * (maximum - 1) + ["agent-1"])
            + ")"
        )
        created = [r for r in results if isinstance(r, AgentCreated)]
        assert [c.slot for c in created] == list(LETTERS[: maximum - 1])
        assert len(set(holders.values())) == maximum
        for c in created:
            await s.until(is_type("agent.status", agent_id=c.agent_id, state="ready_for_review"))
        rejected = created[2]  # C, outside the old A/B capacity.
        response = await s.client.post(
            f"/changes/{rejected.change_id}/reject", json=observed_body(s, rejected.change_id)
        )
        assert response.status_code == 202
        await s.until(is_type("agent.status", agent_id=rejected.agent_id, state="done"))
        assert holders["C"] is None and all(holders[slot] for slot in holders if slot != "C")
        assert (await start(s)).json()["slot"] == "C"


@pytest.mark.parametrize("failure", ["api", "state", "exception", "cancel"])
async def test_failed_start_releases_only_f(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    live = LiveRun(fake_settings(tmp_path, 6))
    errors = {
        "api": ApiError("invalid_request", "refused"),
        "state": WrongState("suspect"),
        "exception": RuntimeError("failed"),
        "cancel": asyncio.CancelledError(),
    }
    manager = Mock(spec=ChangeManager, main_suspect=False)
    manager.open = AsyncMock(side_effect=errors[failure])
    monkeypatch.setattr(live, "_manager", manager)
    monkeypatch.setattr(live, "_report", AsyncMock())
    holders = live._slots  # pyright: ignore[reportPrivateUsage]
    holders["A"] = "agent-existing"
    before = holders.copy()
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ApiError):
        await live.start(StartAgent(task="t", slot="F"))
    assert holders == before
    manager.open.assert_awaited_once()


async def test_omitted_slot_skips_failed_checks_but_named_slot_is_not_rerouted(
    tmp_path: Path,
) -> None:
    settings = replace(fake_settings(tmp_path, 6), agent_model_c="muse-spark")
    clients = BySlot(lambda profile, _slot: GatedModel(profile=profile))
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        assert not s.store.allow_fake_provider
        assert (await start(s)).json()["slot"] == "A"
        assert (await start(s)).json()["slot"] == "B"
        assert error_code(await start(s, slot="C")) == (503, "not_configured")
        assert (await start(s)).json()["slot"] == "D"
        assert (await start(s)).json()["slot"] == "E"
        assert (await start(s)).json()["slot"] == "F"


async def test_sparse_fake_slots_keep_letter_order_and_count_only_offered_slots(
    tmp_path: Path,
) -> None:
    settings = replace(
        fake_settings(tmp_path, 6), fake_script_b=None, fake_script_d=None, fake_script_e=None
    )
    clients = BySlot(lambda profile, _slot: GatedModel(profile=profile))
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        assert s.store.allow_fake_provider
        assert [entry.slot for entry in s.events()[0].payload.slots] == ["A", "C", "F"]
        assert error_code(await start(s, slot="B")) == (422, "invalid_request")
        assert [(await start(s)).json()["slot"] for _ in range(3)] == ["A", "C", "F"]
        busy = await start(s)
        assert error_code(busy) == (409, "slot_busy")
        assert (
            busy.json()["error"]["message"] == "all 3 slots are taken (agent-1, agent-2, agent-3)"
        )


async def test_single_slot_keeps_its_busy_wording(tmp_path: Path) -> None:
    clients = BySlot(lambda profile, _slot: GatedModel(profile=profile))
    async with asyncio.timeout(DEADLINE_S), live_server(fake_settings(tmp_path, 1), clients) as s:
        assert (await start(s)).json()["slot"] == "A"
        busy = await start(s)
        assert error_code(busy) == (409, "slot_busy")
        assert busy.json()["error"]["message"] == "slot A is taken (agent-1)"


async def test_fake_reviewer_rules_include_every_offered_author(tmp_path: Path) -> None:
    script = tmp_path / "reviewer.json"
    script.write_text('{"task":"Review","turns":[{"text":"VERDICT: accept"}]}')
    settings = replace(
        fake_settings(tmp_path, 6),
        cross_review=True,
        review_model="fake-b",
        reviewer_fake_b_script=script,
    )
    client = configured_reviewer(settings, "F", FAKE_MODEL)
    assert client is not None
    assert (await client.complete([], [])).text == "VERDICT: accept"
    with pytest.raises(ReviewerUnavailable, match="all-fake profiles"):
        configured_reviewer(replace(settings, agent_model_f="muse-spark"), "C", FAKE_MODEL)
