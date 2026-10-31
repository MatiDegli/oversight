"""Usage response shapes preserve unknowns, finite lower bounds and stable names."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from contracts import generate
from pydantic import Field, TypeAdapter, ValidationError

from oversight_contracts.common import ContractModel, MessageText, NonEmptyText
from oversight_contracts.events import AgentMessageSent, Limits
from oversight_contracts.usage import (
    UsageActor,
    UsageCost,
    UsageCostRow,
    UsageHours,
    UsageTeam,
    UsageView,
)

ROOT = Path(__file__).resolve().parents[2]


def models() -> list[dict[str, Any]]:
    return [
        dict(
            team="agent-3",
            agent_id="agent-3",
            role=None,
            model=None,
            priced=False,
            calls=1,
            known_usd=0.0,
            usd=None,
        ),
        dict(
            source_url="source",
            retrieved="2026-10-30",
            calls=1,
            priced_calls=0,
            unpriced_calls=1,
            ranged_calls=0,
            known_usd=0.0,
            usd=None,
            accepted_changes=0,
            usd_per_accepted=None,
            known_usd_per_accepted=None,
            coverage="missing_metrics",
            problems=[],
            rows=[],
        ),
        dict(
            team=None,
            role="review",
            agent_id=None,
            change_id="change-1",
            model=None,
            ms=None,
            open=False,
        ),
        dict(
            known_agent_ms=0,
            agent_ms=None,
            known_active_ms=0,
            active_ms=None,
            parallelism=None,
            coverage="missing_metrics",
            problems=[],
            actors=[],
        ),
        dict(
            team="plan-2",
            kind="plan",
            agents=[],
            priced=False,
            calls=1,
            known_usd=0.0,
            usd=None,
            known_ms=0,
            ms=None,
        ),
    ]


MODELS = [UsageCostRow, UsageCost, UsageActor, UsageHours, UsageTeam]


def test_schema_has_usage_view() -> None:
    schema = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())
    assert schema["$defs"]["UsageView"]["additionalProperties"] is False
    assert set(schema["$defs"]["UsageView"]["required"]) == {
        "run_id",
        "seq",
        "cost",
        "hours",
        "teams",
        "teams_add_up",
        "revision_rounds",
    }


def test_null_figures_are_accepted() -> None:
    row, cost, actor, hours, team = [
        m.model_validate(v) for m, v in zip(MODELS, models(), strict=True)
    ]
    view = UsageView(
        run_id="run-20261027-185214",
        seq=0,
        cost=cost,
        hours=hours,
        teams=[team],
        teams_add_up=False,
        revision_rounds=None,
    )
    assert row.usd is None and actor.ms is None
    assert UsageView.model_validate_json(view.model_dump_json()) == view


@pytest.mark.parametrize("index", range(5))
def test_negative_figures_and_extra_keys_are_refused(index: int) -> None:
    model, body = MODELS[index], models()[index]
    with pytest.raises(ValidationError):
        model.model_validate({**body, "extra": True})
    for name in body:
        if name in {
            "calls",
            "priced_calls",
            "unpriced_calls",
            "ranged_calls",
            "accepted_changes",
            "known_usd",
            "usd",
            "usd_per_accepted",
            "known_usd_per_accepted",
            "ms",
            "known_ms",
            "known_agent_ms",
            "agent_ms",
            "known_active_ms",
            "active_ms",
            "parallelism",
        }:
            with pytest.raises(ValidationError):
                model.model_validate({**body, name: -1})


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_figures_are_refused(value: float) -> None:
    for model, body in zip(MODELS, models(), strict=True):
        for name in body:
            if name in {
                "known_usd",
                "usd",
                "usd_per_accepted",
                "known_usd_per_accepted",
                "parallelism",
            }:
                with pytest.raises(ValidationError):
                    model.model_validate({**body, name: value})


@pytest.mark.parametrize(
    "team,valid",
    [("agent-3", True), ("plan-2", True), ("other", False), ("agent-0", False), ("plan-0", False)],
)
def test_team_identifier_formats(team: str, valid: bool) -> None:
    body = {**models()[4], "team": team}
    if valid:
        assert UsageTeam.model_validate(body).team == team
    else:
        with pytest.raises(ValidationError):
            UsageTeam.model_validate(body)


def test_generated_names_are_stable_and_readable() -> None:
    schema = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())
    assert not any(re.search(r"__[0-9]", name) for name in schema["$defs"])
    typescript = (ROOT / "contracts/generated/contracts.ts").read_text()
    assert not re.search(r"export (?:type|interface) \w*__[0-9]", typescript)
    assert schema["$defs"]["NonEmptyText"] == {"minLength": 1, "type": "string"}
    assert schema["$defs"]["MessageText"] == {"minLength": 1, "maxLength": 4096, "type": "string"}


def test_bounded_message_text_keeps_its_shape() -> None:
    adapter = TypeAdapter(MessageText)
    assert adapter.validate_python("x" * 4096) == "x" * 4096
    for text in ["", "x" * 4097]:
        with pytest.raises(ValidationError):
            adapter.validate_python(text)
    assert AgentMessageSent(agent_id="agent-1").text is None
    assert AgentMessageSent(agent_id="agent-1", text="hello").text == "hello"


def test_limits_describe_per_round_budgets() -> None:
    assert Limits.model_fields["max_steps"].description == (
        "Tool calls per round; Request changes starts a new round with the same budget."
    )
    assert Limits.model_fields["max_wall_clock_s"].description == (
        "Per round; Request changes starts a new round."
    )


def test_adding_a_response_does_not_rename_existing_definitions(monkeypatch) -> None:
    before = generate.build_schema()["$defs"]

    class AnotherResponse(ContractModel):
        text: NonEmptyText
        bounded: MessageText | None = Field(default=None)

    monkeypatch.setattr(generate, "RESPONSES", [AnotherResponse, *generate.RESPONSES])
    after = generate.build_schema()["$defs"]
    assert set(after) - set(before) == {"AnotherResponse"}
    assert {name: after[name] for name in before} == before


def test_view_refuses_negative_counts_and_extra_keys() -> None:
    body = dict(
        run_id="run-20261027-185214",
        seq=0,
        cost=models()[1],
        hours=models()[3],
        teams=[],
        teams_add_up=False,
        revision_rounds=None,
    )
    for invalid in [{"seq": -1}, {"revision_rounds": -1}, {"extra": True}]:
        with pytest.raises(ValidationError):
            UsageView.model_validate({**body, **invalid})
