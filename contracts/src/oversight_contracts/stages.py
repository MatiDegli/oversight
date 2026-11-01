"""Raw elapsed time per stage of one change, folded from the committed event log."""

from typing import Literal

from pydantic import Field, NonNegativeInt, PositiveInt

from oversight_contracts.common import ContractModel, NonEmptyText


class StageTime(ContractModel):
    """One stage's elapsed time through the view's seq. Stages are never added together."""

    ms: NonNegativeInt | None
    """The stage's total; null when any of its intervals is unknown."""
    known_ms: NonNegativeInt
    """The sum of known intervals; a lower bound when ms is null."""
    intervals: NonNegativeInt
    """Intervals counted, known or not."""
    open: bool
    """Still running at the view's seq; the open interval ends at that event's ts."""


class SuitePhaseTime(ContractModel):
    """Recorded suite time for one phase. Not "TestPhaseTime", which pytest would try to collect."""

    phase: Literal["agent", "proposal", "combined", "base_probe", "head_probe"]
    ms: NonNegativeInt
    """The sum of the phase's recorded duration_ms."""
    runs: PositiveInt


class ChangeStages(ContractModel):
    """Raw elapsed time per stage for one change: never a score, ranking, rate or review time.

    waiting_for_person runs from the end of a revision's automatic checks to the person's
    decision receipt, less the combined test run approval starts; it includes reading. A
    revision decided by rules, cross-review or the lead adds nothing; an unrecorded decider
    makes that interval unknown. test_runs overlaps the other stages.
    """

    agent_work: StageTime
    checks: StageTime
    waiting_for_person: StageTime
    test_runs: StageTime
    test_phases: list[SuitePhaseTime] = Field(default_factory=list, max_length=5)
    """One entry per phase that ran, in the order above."""
    problems: list[NonEmptyText] = Field(default_factory=list, max_length=20)
    """Why an interval is unknown, with the seq that shows it."""
