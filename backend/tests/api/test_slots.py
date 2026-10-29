"""Two agents in parallel (docs/plans/two-slots.md): the claim, the release, the pick, the profiles,
the clients and their combinations, and the one log both runs write."""

import asyncio
import contextlib
from pathlib import Path
from typing import Any, ClassVar

import pytest
from decision_helpers import observed_body
from live_helpers import (
    DEADLINE_S,
    FAKE_SCRIPT,
    FAKE_SCRIPT_B,
    BySlot,
    GatedModel,
    error_code,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    script_json,
    start,
    two_fakes,
    write_ledger,
)

from oversight.agents import (
    BASETEN_BASE_URL,
    META_BASE_URL,
    Script,
    ScriptedModel,
    parse_script,
)
from oversight.api import ApiError
from oversight.app import LIMITS, LiveRun
from oversight.changes import ChangeManager
from oversight_contracts.commands import StartAgent
from oversight_contracts.common import Slot
from oversight_contracts.events import ChangeResolved, Conflict, ModelProfile

pytestmark = pytest.mark.anyio

FINISH = script_json(
    "Finish at once.", [{"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}]}]
)


def gated_clients() -> tuple[GatedModel, GatedModel, BySlot]:
    """Each slot's model waits at its first call, so the run stays `working`."""
    a, b = GatedModel(), GatedModel()
    return a, b, BySlot(lambda _profile, slot: a if slot == "A" else b)


def scripted(text: str) -> Script:
    return parse_script(text)


# --- The claim and the pick ----------------------------------------------------------------------


async def test_concurrent_starts_take_a_then_b_then_busy(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path)) as s:
        release = asyncio.Event()

        async def build() -> None:
            await release.wait()

        # Every start waits for the venv build, after it claimed its slot: held, three starts are in
        # flight at once. Started eagerly, each runs up to that wait before the next begins.
        s.live._venv = asyncio.create_task(build())  # pyright: ignore[reportPrivateUsage]
        loop = asyncio.get_running_loop()
        first, second, third = (
            asyncio.Task(s.live.start(StartAgent(task="t")), loop=loop, eager_start=True)
            for _ in range(3)
        )
        assert s.live._slots == {"A": "starting", "B": "starting"}  # pyright: ignore[reportPrivateUsage]
        assert third.done()
        refused = third.exception()
        assert isinstance(refused, ApiError) and refused.code == "slot_busy"
        assert refused.message == "both slots are taken (starting, starting)"
        release.set()
        created = [await first, await second]
        assert [(c.agent_id, c.slot) for c in created] == [("agent-1", "A"), ("agent-2", "B")]
        assert s.live._slots == {"A": "agent-1", "B": "agent-2"}  # pyright: ignore[reportPrivateUsage]
        for agent in created:
            await s.until(
                is_type("agent.status", agent_id=agent.agent_id, state="ready_for_review")
            )


async def test_slot_rules(tmp_path: Path) -> None:
    _, _, clients = gated_clients()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), clients) as s:
        # With no slot: A while it is free, then B.
        assert (await start(s)).json()["slot"] == "A"
        assert (await start(s)).json()["slot"] == "B"
        # Both held: no slot, and a named one, are refused, and a named one is never rerouted.
        both = await start(s)
        assert error_code(both) == (409, "slot_busy")
        assert both.json()["error"]["message"] == "both slots are taken (agent-1, agent-2)"
        named = await start(s, slot="B")
        assert error_code(named) == (409, "slot_busy")
        assert named.json()["error"]["message"] == "slot B is taken (agent-2)"
        assert clients.built == [("fake", "A"), ("fake", "B")]


async def test_a_failed_open_in_b_frees_only_b(tmp_path: Path) -> None:
    _, _, clients = gated_clients()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), clients) as s:
        assert (await start(s, slot="A")).status_code == 201
        # A directory where agent-2's worktree would go makes `open` fail.
        (tmp_path / ".runtime" / "worktrees" / "agent-2").mkdir(parents=True)
        failed = await start(s, slot="B")
        assert error_code(failed) == (500, "internal")
        assert s.live._slots == {"A": "agent-1", "B": None}  # pyright: ignore[reportPrivateUsage]
        retried = await start(s, slot="B")
        assert (retried.status_code, retried.json()["slot"]) == (201, "B")


async def test_a_cancelled_start_in_b_frees_only_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    real_open = ChangeManager.open

    async def held_open(self: ChangeManager, task: Any, model_id: str, **kwargs: Any) -> Any:
        if task.slot == "B":
            entered.set()
            await asyncio.Event().wait()  # never set: only a cancel ends it
        return await real_open(self, task, model_id, **kwargs)

    _, _, clients = gated_clients()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), clients) as s:
        monkeypatch.setattr(ChangeManager, "open", held_open)
        assert (await start(s, slot="A")).status_code == 201
        pending = asyncio.create_task(s.live.start(StartAgent(task="t", slot="B")))
        await entered.wait()
        assert s.live._slots == {"A": "agent-1", "B": "starting"}  # pyright: ignore[reportPrivateUsage]
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending
        assert s.live._slots == {"A": "agent-1", "B": None}  # pyright: ignore[reportPrivateUsage]


def resolved(agent_id: str, decision: str, merge: str | None) -> ChangeResolved:
    conflict = Conflict(kind="merge", paths=["shop/pricing.py"], tests=None)
    return ChangeResolved.model_validate(
        {
            "change_id": f"change-{agent_id.removeprefix('agent-')}",
            "agent_id": agent_id,
            "revision": 1,
            "decision": decision,
            "decided_by": "person",
            "head_commit": "observed-head",
            "reason": None,
            "comment": None,
            "merge": merge,
            "merge_commit": "a" * 40 if merge == "merged" else None,
            "conflict": conflict.model_dump() if merge == "conflict" else None,
        }
    )


@pytest.mark.parametrize(
    ("decision", "merge", "frees"),
    [
        ("rejected", None, True),
        ("approved", "merged", True),
        ("approved", "conflict", False),
        ("changes_requested", None, False),
    ],
)
async def test_a_resolution_frees_only_its_slot(
    tmp_path: Path, decision: str, merge: str | None, frees: bool
) -> None:
    # A rejection and a landed merge give the slot back; a conflict and a request for changes keep
    # it, as the change lives on. Either way the other slot's holder is untouched.
    _, _, clients = gated_clients()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), clients) as s:
        assert (await start(s)).status_code == 201
        assert (await start(s)).status_code == 201
        await s.live._publish(resolved("agent-2", decision, merge))  # pyright: ignore[reportPrivateUsage]
        assert s.live._slots == {"A": "agent-1", "B": None if frees else "agent-2"}  # pyright: ignore[reportPrivateUsage]


async def test_a_decided_run_frees_its_slot_and_the_next_start_takes_the_first_free(
    tmp_path: Path,
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path)) as s:
        assert (await start(s, slot="B")).status_code == 201
        assert (await start(s)).json()["slot"] == "A"
        for agent in ("agent-1", "agent-2"):
            await s.until(is_type("agent.status", agent_id=agent, state="ready_for_review"))
        assert (
            await s.client.post("/changes/change-1/reject", json=observed_body(s, "change-1"))
        ).status_code == 202  # B's change
        await s.until(is_type("agent.status", agent_id="agent-1", state="done"))
        assert s.live._slots == {"A": "agent-2", "B": None}  # pyright: ignore[reportPrivateUsage]
        assert (
            await s.client.post("/changes/change-2/reject", json=observed_body(s, "change-2"))
        ).status_code == 202
        await s.until(is_type("agent.status", agent_id="agent-2", state="done"))
        assert s.live._slots == {"A": None, "B": None}  # pyright: ignore[reportPrivateUsage]
        assert (await start(s)).json()["slot"] == "A"  # A before B again


# --- Profiles, clients and the one run.started ---------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "slots", "models"),
    [
        ({"fake_script_b": FAKE_SCRIPT_B}, [("A", "fake"), ("B", "fake")], ["fake"]),
        ({"fake_script_b": None}, [("A", "fake")], ["fake"]),
        (
            {"agent_model": "muse-spark", "agent_model_b": "muse-spark-open"},
            [("A", "muse-spark"), ("B", "muse-spark-open")],
            ["muse-spark", "muse-spark-open"],
        ),
        (
            {"agent_model": "muse-spark", "agent_model_b": "fake", "fake_script_b": FAKE_SCRIPT_B},
            [("A", "muse-spark"), ("B", "fake")],
            ["muse-spark", "fake"],
        ),
        ({"agent_model": "muse-spark"}, [("A", "muse-spark"), ("B", "muse-spark")], ["muse-spark"]),
    ],
    ids=["two-fakes", "fake-b-without-script", "meta-and-baseten", "meta-and-fake", "meta-twice"],
)
async def test_run_started_lists_the_offered_slots(
    tmp_path: Path,
    overrides: dict[str, Any],
    slots: list[tuple[str, str]],
    models: list[str],
) -> None:
    # A fake B with no script of its own is not offered: A's script would propose A's edit twice.
    # `models` holds each offered profile once, in slot order.
    settings = meta_settings(tmp_path, **({"agent_model": "fake"} | overrides))
    async with asyncio.timeout(DEADLINE_S), live_server(settings) as s:
        run = s.events()[0].payload
    assert [(slot.slot, slot.default_model) for slot in run.slots] == slots
    assert [model.id for model in run.models] == models


def test_a_slot_names_the_variable_of_an_unknown_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OVERSIGHT_AGENT_MODEL_B must be one of"):
        LiveRun(two_fakes(tmp_path, agent_model_b="gpt"))
    with pytest.raises(ValueError, match="OVERSIGHT_AGENT_MODEL must be one of"):
        LiveRun(two_fakes(tmp_path, agent_model="gpt"))


async def test_each_start_builds_its_own_client(tmp_path: Path) -> None:
    # The real factory: each slot plays its own script, so the two edits are different ones.
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path)) as s:
        assert (await start(s, slot="A")).status_code == 201
        assert (await start(s, slot="B")).status_code == 201
        for agent in ("agent-1", "agent-2"):
            await s.until(is_type("agent.status", agent_id=agent, state="ready_for_review"))
        proposals = [e.payload for e in s.events() if e.type == "change.proposed"]
    proposed = {p.change_id: [f.path for f in p.files] for p in proposals}
    assert proposed == {"change-1": ["shop/pricing.py"], "change-2": ["shop/inventory.py"]}
    assert [p.tests.outcome for p in proposals] == ["passed", "passed"]


class SpyClient(ScriptedModel):
    """Stands in for `OpenAIClient`: it records what the server built it with, and calls nothing."""

    made: ClassVar[list["SpyClient"]] = []

    def __init__(
        self, profile: ModelProfile, *, base_url: str, api_key: str, max_output_tokens: int
    ) -> None:
        super().__init__(scripted(FINISH), profile)
        self.base_url, self.api_key, self.max_output_tokens = base_url, api_key, max_output_tokens
        SpyClient.made.append(self)


@pytest.mark.usefixtures("baseten_verified")
async def test_the_client_endpoint_follows_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oversight.app.OpenAIClient", SpyClient)
    monkeypatch.setattr(SpyClient, "made", [])
    settings = meta_settings(
        tmp_path,
        meta_api_key="test-key-meta",
        baseten_api_key="test-key-baseten",
        agent_model_b="muse-spark-open",
    )
    async with asyncio.timeout(DEADLINE_S), live_server(settings) as s:
        assert (await start(s, slot="A")).status_code == 201
        assert (await start(s, slot="B")).status_code == 201
    made = [(c.profile.id, c.base_url, c.api_key, c.max_output_tokens) for c in SpyClient.made]
    assert made == [
        ("muse-spark", META_BASE_URL, "test-key-meta", LIMITS.max_output_tokens),
        ("muse-spark-open", BASETEN_BASE_URL, "test-key-baseten", LIMITS.max_output_tokens),
    ]


# --- The brake and the combinations --------------------------------------------------------------


def scripted_clients() -> BySlot:
    """Every start gets the fake script that finishes at once, on the profile it was asked for."""
    return BySlot(lambda profile, _slot: ScriptedModel(scripted(FINISH), profile))


async def test_the_brake_refuses_the_metered_slot_only(tmp_path: Path) -> None:
    # A on the fake model, B on Muse Spark, 136 of the 150 credits spent: past the 90% line.
    settings = meta_settings(
        tmp_path, agent_model="fake", agent_model_b="muse-spark", fake_script=FAKE_SCRIPT
    )
    write_ledger(settings, {"spent_usd": 136.0, "unknown": []})
    clients = scripted_clients()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        refused = await start(s, slot="B")
        assert error_code(refused) == (403, "spend_brake")
        assert refused.json()["error"]["message"] == s.live.brake_reason()
        assert clients.built == []
        assert (await start(s, slot="A")).status_code == 201
    assert clients.built == [("fake", "A")]


async def test_an_omitted_slot_skips_a_braked_one(tmp_path: Path) -> None:
    # A on Muse Spark, braked; B on the fake model. A start with no slot goes to B; one that names
    # A is refused, not rerouted.
    settings = meta_settings(tmp_path, agent_model_b="fake", fake_script_b=FAKE_SCRIPT_B)
    write_ledger(settings, {"spent_usd": 136.0, "unknown": []})
    clients = scripted_clients()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        assert error_code(await start(s, slot="A")) == (403, "spend_brake")
        created = await start(s)
        assert (created.status_code, created.json()["slot"]) == (201, "B")
        assert clients.built == [("fake", "B")]


async def test_an_omitted_slot_with_both_braked_is_refused_with_the_brake(tmp_path: Path) -> None:
    settings = meta_settings(tmp_path)  # A and B both on Muse Spark
    write_ledger(settings, {"spent_usd": 136.0, "unknown": []})
    clients = scripted_clients()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        refused = await start(s)
        assert error_code(refused) == (403, "spend_brake")
        assert refused.json()["error"]["message"] == s.live.brake_reason()
    assert clients.built == []


@pytest.mark.usefixtures("baseten_verified")
@pytest.mark.parametrize(
    ("overrides", "body", "status", "message", "built"),
    [
        # A on Muse Spark, B on the fake model with its own script.
        (
            {"agent_model_b": "fake", "fake_script_b": FAKE_SCRIPT_B},
            {"slot": "A", "model": "fake"},
            422,
            "slot A has no script for fake",
            [],
        ),
        (
            {"agent_model_b": "fake", "fake_script_b": FAKE_SCRIPT_B},
            {"slot": "B", "model": "fake"},
            201,
            None,
            [("fake", "B")],
        ),
        (
            {"agent_model_b": "fake", "fake_script_b": FAKE_SCRIPT_B},
            {"slot": "B", "model": "muse-spark"},
            201,
            None,
            [("muse-spark", "B")],
        ),
        # A on Muse Spark, B on Muse Spark Open: each may take the other's profile.
        (
            {"agent_model_b": "muse-spark-open", "baseten_api_key": "test-key"},
            {"slot": "A", "model": "muse-spark-open"},
            201,
            None,
            [("muse-spark-open", "A")],
        ),
        (
            {"agent_model_b": "muse-spark-open", "baseten_api_key": "test-key"},
            {"slot": "B", "model": "muse-spark"},
            201,
            None,
            [("muse-spark", "B")],
        ),
        # The key follows the provider, not the slot.
        (
            {"agent_model_b": "muse-spark-open"},
            {"slot": "B"},
            503,
            "Server setup is incomplete.",
            [],
        ),
        (
            {
                "agent_model_b": "muse-spark-open",
                "baseten_api_key": "test-key",
                "meta_api_key": None,
            },
            {"slot": "B", "model": "muse-spark"},
            503,
            "Server setup is incomplete.",
            [],
        ),
        # A model no slot runs.
        (
            {"agent_model_b": "muse-spark-open", "baseten_api_key": "test-key"},
            {"slot": "A", "model": "fake"},
            422,
            "this server runs agents on muse-spark, muse-spark-open",
            [],
        ),
    ],
    ids=[
        "fake-on-a",
        "fake-on-b",
        "meta-on-b",
        "baseten-on-a",
        "meta-on-b-with-baseten",
        "baseten-without-its-key",
        "meta-without-its-key",
        "unoffered-model",
    ],
)
async def test_slot_model_combinations(
    tmp_path: Path,
    overrides: dict[str, Any],
    body: dict[str, str],
    status: int,
    message: str | None,
    built: list[tuple[str, Slot]],
) -> None:
    clients = scripted_clients()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(meta_settings(tmp_path, **overrides), clients) as s,
    ):
        response = await start(s, **body)
        assert response.status_code == status
        if message is not None:
            error = response.json()["error"]
            assert error["message"] == message
            assert error["code"] == ("not_configured" if status == 503 else "invalid_request")
            if status == 503:
                name = "META_API_KEY" if body.get("model") == "muse-spark" else "BASETEN_API_KEY"
                assert error["missing"] == [{"name": name, "problem": "missing", "slots": ["B"]}]
            else:
                assert "missing" not in error
    assert clients.built == built


async def test_an_unverified_profile_is_refused_on_either_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oversight.app.UNVERIFIED_PROFILES", frozenset({"muse-spark-open"}))
    settings = meta_settings(tmp_path, agent_model_b="muse-spark-open", baseten_api_key="test-key")
    clients = scripted_clients()
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        for body in ({"slot": "A", "model": "muse-spark-open"}, {"slot": "B"}):
            refused = await start(s, **body)
            assert error_code(refused) == (422, "invalid_request")
            assert "muse-spark-open cannot run agents yet" in refused.json()["error"]["message"]
    assert clients.built == []


async def test_fake_a_alone_has_no_slot_b(tmp_path: Path) -> None:
    # A on the fake model and B with no script of its own: B is not offered, with the constant open.
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        assert error_code(await start(s, slot="B")) == (422, "invalid_request")
        assert (await start(s)).json()["slot"] == "A"


async def test_fake_refusal_names_the_requested_profile(tmp_path: Path) -> None:
    settings = live_settings(tmp_path, agent_model_c="fake-b", fake_script_c=FAKE_SCRIPT_B)
    async with asyncio.timeout(DEADLINE_S), live_server(settings) as s:
        refused = await start(s, slot="A", model="fake-b")
        assert error_code(refused) == (422, "invalid_request")
        assert refused.json()["error"]["message"] == "slot A has no script for fake-b"
        assert (await start(s, slot="A", model="fake")).status_code == 201
