"""Admission for person-configured seats retains atomic reservations and per-slot scripts."""

import asyncio
import string
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
LETTERS: tuple[Slot, ...] = (*string.ascii_uppercase, "AA", "AB")


def fake_settings(tmp_path: Path, maximum: int) -> Settings:
    """Each offered slot has a distinct script, edit and finish summary; fixtures stay intact."""
    paths: dict[str, Path] = {}
    scripts: dict[Slot, Path] = {}
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
        if slot == "A":
            paths["fake_script"] = path
        elif slot in "BCDEF":
            paths[f"fake_script_{slot.lower()}"] = path
        else:
            scripts[slot] = path
    if scripts:
        return live_settings(
            tmp_path, harness_pack=False, max_agents=maximum, fake_scripts=scripts, **paths
        )
    return live_settings(tmp_path, harness_pack=False, max_agents=maximum, **paths)


@pytest.mark.parametrize("maximum", [4, 6, 11])
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


@pytest.mark.parametrize("maximum", [4, 6, 11])
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


@pytest.mark.parametrize("maximum", [4, 6, 11])
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


async def test_two_leads_run_five_and_six_children_and_twelfth_waits(tmp_path: Path) -> None:
    from oversight_contracts.plans import PlanZone, SubtaskDraft

    clients = BySlot(lambda profile, _slot: GatedModel(profile=profile))
    settings = replace(fake_settings(tmp_path, 11), lead_max_subtasks=12)
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        plans = []
        for count in (5, 6):
            lead = await s.client.post("/leads", json={"task": f"Coordinate {count} agents"})
            assert lead.status_code == 201
            drafts = [
                SubtaskDraft(
                    task=f"Task {i}", zone=PlanZone(node_ids=[], paths=["shop/pricing.py"])
                )
                for i in range(count)
            ]
            created = await s.live.plans.create_and_propose(
                "Coordinate", drafts, started_by=lead.json()["agent_id"]
            )
            plan = s.live.plans.get(created.plan_id)
            response = await s.client.post(
                f"/plans/{plan.plan_id}/start", json={"revision": plan.revision}
            )
            assert response.status_code == 202, response.text
            await s.until(
                lambda event, plan=plan, count=count: (
                    is_type("plan.updated", plan_id=plan.plan_id)(event)
                    and sum(t.agent_id is not None for t in event.payload.subtasks or []) == count
                )
            )
            plans.append(s.live.plans.get(plan.plan_id))
        children = [t for plan in plans for t in plan.subtasks or []]
        assert len(children) == len({t.agent_id for t in children}) == 11
        assert all(t.state == "running" for t in children)
        assert len([value for value in s.live._slots.values() if value]) == 11  # pyright: ignore[reportPrivateUsage]
        # First-wave validation continues to compare against the run's actual seat count.
        with pytest.raises(ApiError, match=r"OVERSIGHT_MAX_AGENTS=11"):
            s.live.plans.check_first_wave([drafts[0]] * 12)
        response = await s.client.post(
            "/queue",
            json={"agent": {"task": "Twelfth child", "slot": None, "model": None, "zone": None}},
        )
        assert response.status_code == 201, response.text
        identifier = response.json()["queued_task_id"]
        await s.until(is_type("queue.updated", queued_task_id=identifier, state="queued"))
        item = s.live.dispatcher.items[identifier]
        assert not item.agent_ids
        first = children[0].agent_id
        assert first is not None
        assert (await s.client.post(f"/agents/{first}/stop")).status_code == 202
        await s.until(is_type("queue.updated", queued_task_id=identifier, state="running"))
        item = s.live.dispatcher.items[identifier]
        assert len(item.agent_ids) == 1 and item.agent_ids[0] not in {t.agent_id for t in children}
        assert s.live._slots["A"] == item.agent_ids[0]  # pyright: ignore[reportPrivateUsage]
        assert all(s.live.plans.get(plan.plan_id).started_by == plan.started_by for plan in plans)
