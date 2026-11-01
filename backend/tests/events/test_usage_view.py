"""The aggregate maps the folds and uses the maintainer's team identity rule."""

from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from oversight.events import build_usage_view, fold_activity, price_usage, read_jsonl
from oversight_contracts.events import ServerEvent

RUN = "run-20261027-185214"
MODEL = "muse-spark-1.3-contributor"
DEMO = Path(__file__).resolve().parents[3] / "fixtures/events/recorded-demo.jsonl"
EVENT = TypeAdapter(ServerEvent)


class Log:
    def __init__(self) -> None:
        self.events: list[ServerEvent] = []

    def add(self, kind: str, **payload: Any) -> None:
        seq = len(self.events) + 1
        self.events.append(
            EVENT.validate_python(
                dict(seq=seq, ts=f"2026-10-31T12:00:{seq:02d}.000Z", type=kind, payload=payload)
            )
        )

    def status(self, agent: str = "agent-1", state: str = "working", **extra: Any) -> None:
        self.add(
            "agent.status",
            **dict(
                dict(
                    agent_id=agent,
                    slot="A",
                    change_id="change-1",
                    task="Work",
                    model=MODEL,
                    state=state,
                    step=0,
                    max_steps=10,
                    detail=None,
                ),
                **extra,
            ),
        )

    def tick(self, agent: str = "agent-1", **extra: Any) -> None:
        self.add(
            "usage.tick",
            **dict(
                dict(
                    agent_id=agent,
                    model=MODEL,
                    role="agent",
                    input_tokens=100,
                    cached_tokens=0,
                    output_tokens=100,
                    cost_usd=987.0,
                    spent_usd=999.0,
                    budget_usd=1000.0,
                    brake="blocked",
                ),
                **extra,
            ),
        )

    def plan(self, plan: str = "plan-1", lead: str | None = None) -> None:
        self.add(
            "plan.updated",
            plan_id=plan,
            revision=1,
            task="Plan",
            graph_version="g",
            started_by=lead,
        )

    def lead(self, agent: str = "agent-9") -> None:
        self.add(
            "lead.status",
            agent_id=agent,
            revision=1,
            task="Coordinate",
            model=MODEL,
            state="working",
            step=0,
            detail=None,
        )

    def review(self, change: str = "change-1") -> None:
        self.add(
            "change.reviewed",
            change_id=change,
            revision=1,
            base_commit="base",
            head_commit="head",
            main_commit="main",
            candidate_tree="tree",
            author_model=MODEL,
            reviewer_model="review-model",
            reviewer_provider="fake",
            reasoning=None,
            verdict="clean",
            findings=[],
            complete=True,
            problems=[],
        )

    def request(self, change: str = "change-1") -> None:
        self.add(
            "change.resolved",
            change_id=change,
            agent_id="agent-1",
            revision=1,
            decision="changes_requested",
            decided_by="person",
            reason=None,
            comment="Revise",
            merge=None,
            merge_commit=None,
            conflict=None,
        )

    def view(self):
        return build_usage_view(RUN, self.events)


def complete_log() -> Log:
    log = Log()
    log.status()
    log.status("agent-2", change_id="change-2")
    log.tick()
    log.tick("agent-2")
    log.status(state="done")
    log.status("agent-2", "done", change_id="change-2")
    return log


def test_recorded_demo_usage_view() -> None:
    view = build_usage_view(RUN, read_jsonl(DEMO))
    assert (view.run_id, view.seq) == (RUN, 69)
    cost = view.cost
    assert (cost.calls, cost.priced_calls, cost.unpriced_calls, cost.ranged_calls) == (17, 7, 10, 0)
    assert cost.known_usd == pytest.approx(0.000977554, abs=1e-12)
    assert cost.usd is None and cost.coverage == "missing_metrics"
    assert cost.accepted_changes == 2 and cost.usd_per_accepted is None
    assert cost.known_usd_per_accepted == pytest.approx(0.000488777, abs=1e-12)
    a, b = cost.rows
    assert (a.agent_id, a.team, a.priced) == ("agent-1", "agent-1", True)
    assert a.usd == pytest.approx(0.000977554, abs=1e-12)
    assert (b.agent_id, b.team, b.priced, b.usd) == ("agent-2", "agent-2", False, None)
    hours = view.hours
    assert (hours.agent_ms, hours.active_ms, hours.coverage) == (32982, 21457, "complete")
    assert (hours.known_agent_ms, hours.known_active_ms) == (32982, 21457)
    assert hours.parallelism == 32982 / 21457
    assert [(a.agent_id, a.team, a.ms) for a in hours.actors] == [
        ("agent-1", "agent-1", 21457),
        ("agent-2", "agent-2", 11525),
    ]
    a_team, b_team = view.teams
    assert (a_team.team, a_team.kind, a_team.agents, a_team.calls, a_team.ms) == (
        "agent-1",
        "agent",
        ["agent-1"],
        7,
        21457,
    )
    assert (b_team.team, b_team.priced, b_team.calls, b_team.known_usd, b_team.usd, b_team.ms) == (
        "agent-2",
        False,
        10,
        0.0,
        None,
        11525,
    )
    assert not view.teams_add_up and view.revision_rounds == 0


@pytest.mark.parametrize("demo", [True, False])
def test_view_copies_the_folds(demo: bool) -> None:
    events = read_jsonl(DEMO) if demo else complete_log().events
    view = build_usage_view(RUN, events)
    for fold, mapped, omitted in [
        (price_usage(events), view.cost, {"through_seq", "groups"}),
        (fold_activity(events), view.hours, {"through_seq", "groups", "groups_add_up", "actors"}),
    ]:
        for field in fields(fold):
            if field.name not in omitted:
                value = getattr(fold, field.name)
                assert getattr(mapped, field.name) == (
                    list(value) if isinstance(value, tuple) else value
                )


def test_person_plan_is_one_team() -> None:
    log = Log()
    log.plan()
    log.status("agent-2", plan_id="plan-1")
    log.status(plan_id="plan-1")
    log.tick("agent-8", plan_id="plan-1", role="plan")
    log.tick("agent-2")
    view = log.view()
    (team,) = view.teams
    assert (team.team, team.kind, team.agents, team.calls) == (
        "plan-1",
        "plan",
        ["agent-2", "agent-1"],
        2,
    )
    assert all(row.team == "plan-1" for row in view.cost.rows)
    assert team.known_usd == view.cost.known_usd
    assert view.hours.coverage == "missing_metrics" and team.ms is None


def test_two_plans_of_one_lead_are_one_team() -> None:
    log = Log()
    log.plan(lead="agent-9")
    log.plan("plan-2", "agent-9")
    log.status(plan_id="plan-1")
    log.status("agent-2", plan_id="plan-2")
    log.tick("agent-9", role="lead")
    log.lead()
    (team,) = log.view().teams
    assert (team.team, team.kind, team.agents, team.ms) == (
        "agent-9",
        "lead",
        ["agent-1", "agent-2"],
        None,
    )


def test_standalone_agent_is_its_own_team() -> None:
    log = Log()
    log.status(state="paused")
    (team,) = log.view().teams
    assert (team.team, team.kind, team.agents, team.priced, team.calls, team.usd, team.ms) == (
        "agent-1",
        "agent",
        ["agent-1"],
        True,
        0,
        0.0,
        0,
    )


def test_status_started_by_is_not_read() -> None:
    log = Log()
    log.status(started_by="agent-9")
    (team,) = log.view().teams
    assert (team.team, team.kind) == ("agent-1", "agent")


@pytest.mark.parametrize("author", [True, False])
def test_review_counts_in_the_author_team(author: bool) -> None:
    log = Log()
    log.plan()
    if author:
        log.status(state="ready_for_review", plan_id="plan-1")
    log.tick(role="review", change_id="change-1")
    log.review()
    view = log.view()
    actor = view.hours.actors[-1]
    assert actor.agent_id is None and actor.role == "review"
    assert actor.team == ("plan-1" if author else None)
    assert view.cost.rows[0].team == ("plan-1" if author else "agent-1")
    assert view.teams_add_up == author
    if author:
        assert view.teams[0].ms == view.hours.agent_ms == 2000
    else:
        assert actor.ms is None


def test_unknown_is_never_zero() -> None:
    log = Log()
    log.tick(model="unlisted")
    view = log.view()
    assert view.cost.rows[0].usd is view.teams[0].usd is None
    assert view.cost.rows[0].known_usd == view.teams[0].known_usd == 0.0
    assert view.teams[0].ms is None and view.teams[0].known_ms == 0
    log = complete_log()
    log.tick(model="unlisted", input_tokens=0, cached_tokens=0, output_tokens=0)
    view = log.view()
    assert view.cost.rows[-1].usd == 0.0 and not view.cost.rows[-1].priced
    assert view.teams[0].usd is not None and view.teams_add_up
    log.lead()
    view = log.view()
    assert all(team.ms is None for team in view.teams)
    assert view.teams[1].known_ms == 4000


def test_priced_call_with_missing_input_keeps_totals_unknown() -> None:
    log = Log()
    log.status()
    log.tick(input_tokens=None)
    log.status(state="done")
    view = log.view()
    (row,) = view.cost.rows
    (team,) = view.teams
    assert row.priced and team.priced
    assert row.usd is team.usd is view.cost.usd is None
    assert row.known_usd == team.known_usd == view.cost.known_usd == pytest.approx(0.00002)
    assert (view.cost.priced_calls, view.cost.unpriced_calls, view.cost.ranged_calls) == (1, 0, 1)
    assert view.cost.coverage == "missing_metrics" and view.hours.coverage == "complete"
    assert not view.teams_add_up


def test_teams_add_up_only_when_complete() -> None:
    log = complete_log()
    view = log.view()
    assert view.teams_add_up
    assert sum(team.ms or 0 for team in view.teams) == view.hours.agent_ms
    assert sum(team.usd or 0 for team in view.teams) == view.cost.usd
    log.tick(model="unlisted")
    assert not log.view().teams_add_up


def test_conflicting_plan_ids_keep_the_first() -> None:
    log = Log()
    log.status(plan_id="plan-2")
    log.tick(plan_id="plan-1")
    log.status(state="done", plan_id="plan-3")
    view = log.view()
    assert view.teams[0].team == "plan-2" and view.cost.rows[0].team == "plan-2"
    assert view.cost.coverage == view.hours.coverage == "complete"
    assert not view.teams_add_up


def test_invalid_recorded_cost_leaves_rounds_unknown() -> None:
    log = complete_log()
    before = log.view()
    event = log.events[2]
    assert event.type == "usage.tick"
    log.events[2] = event.model_copy(
        update={"payload": event.payload.model_copy(update={"cost_usd": -1.0})}
    )
    after = log.view()
    assert after.revision_rounds is None
    assert after.cost == before.cost and after.hours == before.hours


def test_rounds_sum_confirmed_requests() -> None:
    log = Log()
    log.request()
    log.request("change-2")
    log.request()
    assert log.view().revision_rounds == 3


def test_team_order() -> None:
    log = Log()
    log.tick("agent-30", role="plan", plan_id="plan-10")
    log.tick("agent-40", role="plan", plan_id="plan-2")
    log.tick("agent-8", role="plan", plan_id="plan-1", model="unlisted")
    log.status("agent-2", "paused", plan_id=None)
    log.status("agent-10", "paused", plan_id=None)
    log.status("agent-12", "paused", plan_id="plan-3")
    log.plan("plan-3", "agent-9")
    log.status("agent-15", "paused", plan_id="plan-4")
    log.plan("plan-4", "agent-3")
    assert [(t.team, t.kind, t.priced) for t in log.view().teams] == [
        ("agent-3", "lead", True),
        ("agent-9", "lead", True),
        ("plan-2", "plan", True),
        ("plan-10", "plan", True),
        ("agent-2", "agent", True),
        ("agent-10", "agent", True),
        ("plan-1", "plan", False),
    ]


def test_latest_plan_lead_and_tick_first_attribution() -> None:
    log = Log()
    log.tick(plan_id="plan-1")
    log.plan(lead="agent-9")
    log.plan(lead="agent-10")
    log.status(plan_id="plan-2")
    view = log.view()
    assert [(t.team, t.kind, t.agents) for t in view.teams] == [("agent-10", "lead", ["agent-1"])]
    assert not view.teams_add_up


@pytest.mark.parametrize("gap", [False, True])
def test_integrity_failures_are_preserved(gap: bool) -> None:
    log = complete_log()
    if gap:
        with pytest.raises(ValueError, match="expected seq"):
            build_usage_view(RUN, log.events[1:])
    else:
        log.events[-1] = log.events[-1].model_copy(update={"ts": "2026-10-31T11:00:00.000Z"})
        view = log.view()
        assert view.hours.coverage == "incomplete"
        assert all(team.ms is None for team in view.teams)
        assert not view.teams_add_up


def test_cost_integrity_suppresses_every_team_total() -> None:
    log = complete_log()
    log.tick(input_tokens=-1)
    view = log.view()
    assert view.cost.coverage == "incomplete" and view.cost.usd is None
    assert all(team.usd is None for team in view.teams)
    assert view.cost.rows[1].usd is not None
    assert not view.teams_add_up
