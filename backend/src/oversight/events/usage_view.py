"""List-price equivalents and observed hours, never a bill and never the budget ledger."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from math import fsum
from typing import Literal

from oversight.events.activity_fold import ActivityFold, fold_activity
from oversight.events.list_prices import ListPriceFold, price_usage
from oversight.events.usage_fold import fold_usage
from oversight_contracts.events import ServerEvent
from oversight_contracts.usage import (
    UsageActor,
    UsageCost,
    UsageCostRow,
    UsageHours,
    UsageTeam,
    UsageView,
)

type _Team = tuple[str, Literal["lead", "plan", "agent"]]


@dataclass
class _Teams:
    agent_plan: dict[str, str] = field(default_factory=dict[str, str])
    plan_lead: dict[str, str | None] = field(default_factory=dict[str, str | None])
    leads: set[str] = field(default_factory=set[str])
    author: dict[str, str] = field(default_factory=dict[str, str])
    coding: dict[str, None] = field(default_factory=dict[str, None])
    conflict: bool = False

    def team_of(self, agent_id: str) -> _Team:
        if agent_id in self.leads:
            return agent_id, "lead"
        plan = self.agent_plan.get(agent_id)
        if plan is not None:
            lead = self.plan_lead.get(plan)
            return (lead, "lead") if lead is not None else (plan, "plan")
        return agent_id, "agent"


def _teams(events: Sequence[ServerEvent]) -> _Teams:
    teams = _Teams()
    for event in events:
        if event.type == "agent.status" or event.type == "usage.tick":
            payload = event.payload
            if payload.plan_id is not None:
                first = teams.agent_plan.setdefault(payload.agent_id, payload.plan_id)
                if first != payload.plan_id:
                    teams.conflict = True
            if event.type == "agent.status":
                teams.author.setdefault(event.payload.change_id, event.payload.agent_id)
                teams.coding.setdefault(event.payload.agent_id, None)
        elif event.type == "plan.updated":
            plan = event.payload
            teams.plan_lead[plan.plan_id] = plan.started_by
            if plan.started_by is not None:
                teams.leads.add(plan.started_by)
        elif event.type == "lead.status":
            teams.leads.add(event.payload.agent_id)
    return teams


def _cost(fold: ListPriceFold, teams: _Teams) -> UsageCost:
    return UsageCost(
        source_url=fold.source_url,
        retrieved=fold.retrieved,
        calls=fold.calls,
        priced_calls=fold.priced_calls,
        unpriced_calls=fold.unpriced_calls,
        ranged_calls=fold.ranged_calls,
        known_usd=fold.known_usd,
        usd=fold.usd,
        accepted_changes=fold.accepted_changes,
        usd_per_accepted=fold.usd_per_accepted,
        known_usd_per_accepted=fold.known_usd_per_accepted,
        coverage=fold.coverage,
        problems=list(fold.problems),
        rows=[
            UsageCostRow(
                team=teams.team_of(row.agent_id)[0],
                agent_id=row.agent_id,
                role=row.role,
                model=row.model,
                priced=row.priced,
                calls=row.calls,
                known_usd=row.known_usd,
                usd=row.usd,
            )
            for row in fold.groups
        ],
    )


def _hours(fold: ActivityFold, teams: _Teams) -> UsageHours:
    actors: list[UsageActor] = []
    for actor in fold.actors:
        agent_id = None if actor.role == "review" else actor.actor_id
        author = teams.author.get(actor.change_id) if actor.change_id is not None else None
        owner = author if actor.role == "review" else agent_id
        actors.append(
            UsageActor(
                team=teams.team_of(owner)[0] if owner is not None else None,
                role=actor.role,
                agent_id=agent_id,
                change_id=actor.change_id,
                model=actor.model,
                ms=actor.ms,
                open=actor.open,
            )
        )
    return UsageHours(
        known_agent_ms=fold.known_agent_ms,
        agent_ms=fold.agent_ms,
        known_active_ms=fold.known_active_ms,
        active_ms=fold.active_ms,
        parallelism=fold.parallelism,
        coverage=fold.coverage,
        problems=list(fold.problems),
        actors=actors,
    )


def _team_rows(cost: UsageCost, hours: UsageHours, teams: _Teams) -> list[UsageTeam]:
    identities = dict(teams.team_of(agent) for agent in teams.coding)
    identities.update(teams.team_of(row.agent_id) for row in cost.rows)
    for actor in hours.actors:
        owner = actor.agent_id
        if owner is None and actor.change_id is not None:
            owner = teams.author.get(actor.change_id)
        if owner is not None:
            team, kind = teams.team_of(owner)
            identities[team] = kind
    result: list[UsageTeam] = []
    for team, kind in identities.items():
        rows = [row for row in cost.rows if row.team == team]
        actors = [actor for actor in hours.actors if actor.team == team]
        known_usd = fsum(row.known_usd for row in rows)
        known_ms = sum(actor.ms for actor in actors if actor.ms is not None)
        result.append(
            UsageTeam(
                team=team,
                kind=kind,
                agents=[agent for agent in teams.coding if teams.team_of(agent)[0] == team],
                priced=all(row.priced for row in rows),
                calls=sum(row.calls for row in rows),
                known_usd=known_usd,
                usd=known_usd
                if cost.coverage != "incomplete" and all(row.usd is not None for row in rows)
                else None,
                known_ms=known_ms,
                ms=known_ms
                if hours.coverage == "complete" and all(actor.ms is not None for actor in actors)
                else None,
            )
        )
    return sorted(
        result,
        key=lambda team: (
            not team.priced,
            ("lead", "plan", "agent").index(team.kind),
            int(team.team.rsplit("-", 1)[1]),
        ),
    )


def build_usage_view(run_id: str, events: Sequence[ServerEvent]) -> UsageView:
    """Map the three pure folds over one committed inventory and apply the team rule."""
    prices = price_usage(events)
    activity = fold_activity(events)
    try:
        usage = fold_usage(events)
    except ValueError:
        revision_rounds = None
    else:
        revision_rounds = sum(change.revision_rounds for change in usage.by_change.values())
    assert prices.through_seq == activity.through_seq
    identities = _teams(events)
    cost = _cost(prices, identities)
    hours = _hours(activity, identities)
    teams = _team_rows(cost, hours, identities)
    return UsageView(
        run_id=run_id,
        seq=prices.through_seq,
        cost=cost,
        hours=hours,
        teams=teams,
        teams_add_up=(
            cost.coverage == hours.coverage == "complete"
            and all(actor.team is not None for actor in hours.actors)
            and not identities.conflict
            and sum(team.ms for team in teams if team.ms is not None) == hours.agent_ms
        ),
        revision_rounds=revision_rounds,
    )
