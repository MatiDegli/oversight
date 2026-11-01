"""Raw stage clocks from a complete committed prefix, including reading time in waiting.

Known limit: the approve click publishes no event. Until a merge-queue event exists,
waiting for an approval ends at the receipt less the combined test run, so a merge that
waited behind another adds that wait. Stages overlap and are never added together.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from oversight.events.clock import parse_ts
from oversight_contracts.events import ChangeResolved, ServerEvent, SuiteExecution
from oversight_contracts.stages import ChangeStages, StageTime, SuitePhaseTime

type _Phase = Literal["agent", "proposal", "combined", "base_probe", "head_probe"]
_PHASES: tuple[_Phase, ...] = ("agent", "proposal", "combined", "base_probe", "head_probe")
_ACTIVE = frozenset({"starting", "working", "revising"})
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _milliseconds(ts: str) -> int:
    return (parse_ts(ts) - _EPOCH) // timedelta(milliseconds=1)


@dataclass(frozen=True)
class _Point:
    seq: int
    ms: int | None


def _point(event: ServerEvent) -> _Point:
    try:
        return _Point(event.seq, _milliseconds(event.ts))
    except (ValueError, TypeError, OverflowError):
        return _Point(event.seq, None)


@dataclass
class _Stage:
    known_ms: int = 0
    intervals: int = 0
    unknown: bool = False
    running: bool = False

    def add(self, ms: int | None, *, running: bool = False) -> None:
        self.intervals += 1
        self.unknown |= ms is None
        self.known_ms += ms if ms is not None else 0
        self.running |= running

    def view(self) -> StageTime:
        return StageTime(
            ms=None if self.unknown else self.known_ms,
            known_ms=self.known_ms,
            intervals=self.intervals,
            open=self.running,
        )


@dataclass
class _Window:
    revision: int
    start: _Point
    receipt: _Point | None = None
    combined: tuple[int, int] | None = None  # duration and receipt seq


@dataclass
class _Fold:
    agent: _Stage = field(default_factory=_Stage)
    checks: _Stage = field(default_factory=_Stage)
    waiting: _Stage = field(default_factory=_Stage)
    tests: _Stage = field(default_factory=_Stage)
    phases: dict[_Phase, tuple[int, int]] = field(default_factory=dict[_Phase, tuple[int, int]])
    problems: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    active: _Point | None = None
    window: _Window | None = None

    def problem(self, seq: int, sentence: str) -> None:
        self.problems.append((seq, sentence))

    def elapsed(self, start: _Point, end: _Point) -> int | None:
        if start.ms is None or end.ms is None or end.ms < start.ms:
            seq = start.seq if start.ms is None else end.seq
            self.problem(seq, f"Timestamp goes back or is invalid at seq {seq}.")
            return None
        return end.ms - start.ms

    def status(self, state: str, point: _Point) -> None:
        if state in _ACTIVE:
            if self.active is None:
                self.active = point
        elif self.active is not None:
            self.agent.add(self.elapsed(self.active, point))
            self.active = None

    def suite(self, payload: SuiteExecution, point: _Point) -> None:
        duration = payload.suite.duration_ms
        if duration < 0:
            self.problem(point.seq, f"Negative suite duration at seq {point.seq}.")
        self.tests.add(duration if duration >= 0 else None)
        ms, runs = self.phases.get(payload.phase, (0, 0))
        self.phases[payload.phase] = (ms + (duration if duration >= 0 else 0), runs + 1)
        if (
            self.window is not None
            and payload.revision == self.window.revision
            and payload.phase == "combined"
        ):
            self.window.combined = (duration, point.seq)

    def close_window(
        self,
        end: _Point,
        reason: Literal["resolved", "replaced", "open"],
        resolution: ChangeResolved | None = None,
    ) -> None:
        window = self.window
        if window is None:
            return
        if window.receipt is not None:
            self.checks.add(self.elapsed(window.start, window.receipt))
            start = window.receipt
        elif reason == "open":
            self.checks.add(self.elapsed(window.start, end), running=True)
            return
        else:
            start = window.start
        if reason == "replaced":
            self.waiting.add(None)
            self.problem(
                end.seq, f"Revision {window.revision} replaced without a decision at seq {end.seq}."
            )
        elif reason == "open":
            self.waiting.add(self.elapsed(start, end), running=True)
        elif resolution is not None:
            self.decision(window, start, end, resolution)
        self.window = None

    def decision(
        self, window: _Window, start: _Point, end: _Point, resolution: ChangeResolved
    ) -> None:
        if resolution.decided_by is None:
            self.waiting.add(None)
            self.problem(
                end.seq, f"No recorded decider for revision {window.revision} at seq {end.seq}."
            )
            return
        if resolution.decided_by != "person":
            return
        elapsed = self.elapsed(start, end)
        if (
            elapsed is not None
            and resolution.decision == "approved"
            and window.combined is not None
        ):
            duration, _ = window.combined
            if duration < 0:
                elapsed = None
                self.problem(
                    end.seq, f"Waiting unknown after negative combined suite at seq {end.seq}."
                )
            elif duration > elapsed:
                elapsed = None
                self.problem(end.seq, f"Combined run longer than the wait at seq {end.seq}.")
            else:
                elapsed -= duration
        self.waiting.add(elapsed)

    def consume(self, event: ServerEvent, point: _Point) -> None:
        match event.type:
            case "agent.status":
                self.status(event.payload.state, point)
            case "change.proposed":
                self.close_window(point, "replaced")
                self.window = _Window(event.payload.revision, point)
            case "change.resolved":
                if self.window is not None and event.payload.revision == self.window.revision:
                    self.close_window(point, "resolved", event.payload)
            case "change.facts_recorded" | "change.reviewed" | "change.assessed":
                if self.window is not None and event.payload.revision == self.window.revision:
                    self.window.receipt = point
            case "tests.executed":
                self.suite(event.payload, point)
            case _:
                pass

    def finish(self, end: _Point) -> ChangeStages:
        if self.active is not None:
            self.agent.add(self.elapsed(self.active, end), running=True)
        self.close_window(end, "open")
        return ChangeStages(
            agent_work=self.agent.view(),
            checks=self.checks.view(),
            waiting_for_person=self.waiting.view(),
            test_runs=self.tests.view(),
            test_phases=[
                SuitePhaseTime(phase=phase, ms=self.phases[phase][0], runs=self.phases[phase][1])
                for phase in _PHASES
                if phase in self.phases
            ],
            problems=[
                sentence for _, sentence in sorted(self.problems, key=lambda item: item[0])[:20]
            ],
        )


def fold_stages(events: Sequence[ServerEvent], change_id: str) -> ChangeStages | None:
    """Fold one change's stages in one pass; incomplete inventories have no stage view.

    Only matching change ids count. Unrelated events still establish continuity and the
    final timestamp. No current clock or producer metadata participates in this read.
    """
    if not events:
        return None
    fold = _Fold()
    point = _Point(0, None)
    for expected, event in enumerate(events, start=1):
        if type(event.seq) is not int or event.seq != expected:
            return None
        point = _point(event)
        if getattr(event.payload, "change_id", None) == change_id:
            fold.consume(event, point)
    return fold.finish(point)
