# ruff: noqa: E501
# Keep the approved model text verbatim.
"""Item 10's aggregate read: list-price equivalents and observed hours over a run's committed log."""

from typing import Literal

from pydantic import Field, FiniteFloat, NonNegativeInt

from oversight_contracts.common import AgentId, ChangeId, ContractModel, NonEmptyText, PlanId, RunId

type UsageCoverage = Literal["complete", "missing_metrics", "incomplete"]
"""complete: every figure is known. missing_metrics: unpriced or partly reported calls, or work with no
observed interval. incomplete: an integrity failure. Unless complete, totals are null and known_* is a lower
bound."""


class UsageCostRow(ContractModel):
    """The calls of one agent, role and model, at the stored list price."""

    team: AgentId | PlanId
    """The row's team, by the rule in UsageTeam."""
    agent_id: AgentId
    """usage.tick's agent: the author for review calls, the planner for plan calls."""
    role: Literal["agent", "review", "plan", "lead"] | None
    """usage.tick's role; null in recordings without one."""
    model: str | None
    """The model id the API reported; null when it reported none."""
    priced: bool
    """The stored price table lists this exact model."""
    calls: NonNegativeInt
    known_usd: FiniteFloat = Field(ge=0)
    """Exact costs plus the lower end of calls with an unreported token count."""
    usd: FiniteFloat | None = Field(ge=0)
    """known_usd when every call is exactly priced; otherwise null, never 0."""


class UsageCost(ContractModel):
    """List-price equivalents from the stored, dated price table: not a bill, and not the budget ledger."""

    source_url: NonEmptyText
    """The provider pricing page the table was extracted from."""
    retrieved: NonEmptyText
    """The ISO date the page was read."""
    calls: NonNegativeInt
    priced_calls: NonNegativeInt
    unpriced_calls: NonNegativeInt
    """Calls with usage on a model the table does not list."""
    ranged_calls: NonNegativeInt
    """Priced calls with an unreported token count."""
    known_usd: FiniteFloat = Field(ge=0)
    usd: FiniteFloat | None = Field(ge=0)
    """known_usd only when coverage is complete; otherwise null."""
    accepted_changes: NonNegativeInt
    """Changes approved and merged, each counted once."""
    usd_per_accepted: FiniteFloat | None = Field(ge=0)
    """usd over accepted changes; null unless usd is known and a change was accepted."""
    known_usd_per_accepted: FiniteFloat | None = Field(ge=0)
    """A lower bound; null with no accepted changes."""
    coverage: UsageCoverage
    problems: list[str]
    rows: list[UsageCostRow]
    """Priced rows first, unpriced rows last."""


class UsageActor(ContractModel):
    """One actor's observed working time, never fixed time buckets."""

    team: AgentId | PlanId | None
    """Null for a review whose change has no agent.status."""
    role: Literal["agent", "review", "lead"]
    agent_id: AgentId | None
    """The coding agent or the lead; null for a review, which counts as its own actor."""
    change_id: ChangeId | None
    """The agent's latest change, or the reviewed change."""
    model: str | None
    ms: NonNegativeInt | None
    """Working milliseconds; null when unknown (lead hours, a review with no start), never 0 for unknown."""
    open: bool
    """Still working at the last committed event, where its interval is clipped."""


class UsageHours(ContractModel):
    """Agent-hours, project active hours and parallelism from status, review and lead events."""

    known_agent_ms: NonNegativeInt
    agent_ms: NonNegativeInt | None
    """Each actor's intervals unioned, actors added; null unless coverage is complete."""
    known_active_ms: NonNegativeInt
    active_ms: NonNegativeInt | None
    """The union of all actors' intervals; null unless coverage is complete."""
    parallelism: FiniteFloat | None = Field(ge=0)
    """agent_ms over active_ms; null when either is null or active_ms is 0."""
    coverage: UsageCoverage
    problems: list[str]
    actors: list[UsageActor]


class UsageTeam(ContractModel):
    """One team, by the maintainer's 2026-10-31 rule shared with team colors: the plan's lead when
    Plan.started_by names one, else the plan, else the agent itself."""

    team: AgentId | PlanId
    kind: Literal["lead", "plan", "agent"]
    agents: list[AgentId]
    """Coding agents with an agent.status in this team, in first-status order."""
    priced: bool
    """Every cost row of the team is priced."""
    calls: NonNegativeInt
    known_usd: FiniteFloat = Field(ge=0)
    usd: FiniteFloat | None = Field(ge=0)
    """known_usd when every row's usd is known and cost coverage is not incomplete; otherwise null."""
    known_ms: NonNegativeInt
    ms: NonNegativeInt | None
    """known_ms only when hours coverage is complete and every member actor's ms is known."""


class UsageView(ContractModel):
    """GET /usage: item 10's aggregate over the run's committed log, live or in a replay.

    Null is unknown and zero is zero: when a figure is null, its known_* sibling is a lower bound.
    """

    run_id: RunId
    seq: NonNegativeInt
    """The last committed seq the figures include."""
    cost: UsageCost
    hours: UsageHours
    teams: list[UsageTeam]
    """Priced teams first, then lead, plan and agent teams, each by id number."""
    teams_add_up: bool
    """Both coverages complete, every actor has a team, and the teams' ms sum to agent_ms."""
    revision_rounds: NonNegativeInt | None
    """Confirmed Request changes across every change; null when a recorded cost is invalid."""
