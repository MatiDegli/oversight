"""Predeclared 10/10 criteria; detection is reported separately from intent classification."""

import re
from collections import Counter

from tools.eval.detection.config import ARMS, PROTOCOL, SUPERSEDED_ARMS
from tools.eval.detection_types import DetectionEvidence
from tools.eval.ledger import Record
from tools.eval.report import metrics
from tools.eval.tasks import BenchError

# Insertion order is the report order. Match recorded strings, without backend imports.
_CAUSES = {
    "method or class not probed": r"unsupported node kind",
    "probe unavailable": r"probe unavailable",
    "version unavailable": r"version unavailable",
    "comparison incomplete": r"(?:behavior|caller) comparison incomplete",
    "repetition incomplete": r"(?:caller )?comparison or repetition incomplete",
    "observations not retained": r"differing observations not retained",
    "other differences unclassified": r"\d+ other differences unclassified",
    "nodes omitted by classification limit": r"\d+ nodes omitted by classification limit",
    "no difference to classify": r"no stable selected difference to classify",
    "no behavior evidence": r"no behavior evidence; empty probe is not equivalence",
    "isolation unavailable": r"isolation unavailable: .*",
    "row witness unavailable": r"row witness unavailable",
    "unstable or equal observation": r"unstable or equal observation",
    "typed identity unavailable": r"typed identity unavailable",
    "display sample truncated": r"display sample truncated",
    "typed display incomplete": r"typed display incomplete",
    **{
        f"review cards {status}": rf"{status}; \d+/\d+ classified"
        for status in ("pending", "truncated", "failed")
    },
    "review deadline": r"review cards: .+: deadline",
    "review deadline exhausted": r"review deadline exhausted; remaining nodes unclassified",
    "classification identity mismatch": r"classification identity mismatch",
    "contractual card limit": r"contractual card limit",
    **{
        f"decision unavailable: {stage}": rf"(?:{stage}|unavailable \({stage}\))"
        for stage in [
            "invalid_response",
            "model",
            "finish_reason",
            "tool_calls",
            "empty_text",
            "reply_length",
            "json",
            "duplicate_key",
            "object",
            "fields",
            "choice",
            "empty_choices",
            "choice_count",
            "message",
            "refusal",
            "legacy_tool_call",
            "transport",
            "deadline",
            "profile",
            "credentials",
            "closed",
            "budget_unconfigured",
            "pricing_unconfigured",
            "budget_blocked",
            "ledger_unknown",
            "ledger_unreliable",
        ]
    },
    "review unavailable": r"(?:publication or classification )?unavailable \([A-Za-z_][\w.]*\)",
}


def cause(reason: str) -> str:
    """Normalize one recorded unavailability reason; preserve unknown reasons as other."""
    if re.fullmatch(_CAUSES["review deadline"], reason):
        return "review deadline"
    if not reason.startswith("isolation unavailable: "):
        reason = reason.removeprefix("review cards: ").rsplit(": ", 1)[-1]
    return next(
        (name for name, pattern in _CAUSES.items() if re.fullmatch(pattern, reason)), "other"
    )


def _question_sources(rows: list[DetectionEvidence]) -> dict[str, int]:
    return {
        "questions_from_tests": sum(r.question and not r.tests_passed for r in rows),
        "questions_from_evidence": sum(
            r.question and r.tests_passed and bool(r.unavailable) for r in rows
        ),
        "questions_from_model": sum(
            r.question and r.tests_passed and not r.unavailable for r in rows
        ),
    }


def _question_suffix(group: dict) -> str:
    return (
        f"; questions from failing tests {group['questions_from_tests']}/10, "
        f"missing evidence {group['questions_from_evidence']}/10, "
        f"the model {group['questions_from_model']}/10"
    )


def repetition(records: list[Record], answers: dict[str, str]) -> dict:
    rows = [r.result.detection for r in records if r.result and r.result.detection]
    ids = [r.patch_id for r in rows]
    complete = (
        len(ids) == 20
        and set(ids) == set(answers)
        and all(r.status == "finished" and not r.unknown for r in records)
    )
    groups = {}
    for answer in ("unasked_difference", "only_asked"):
        selected = [r for r in rows if answers.get(r.patch_id) == answer]
        causes = Counter(
            name for r in selected for name in {cause(reason) for reason in r.unavailable}
        )
        groups[answer] = {
            "denominator": 10,
            "executed": len(selected),
            "let_through": sum(r.let_through for r in selected),
            "questions": sum(r.question for r in selected),
            "unavailable_questions": sum(r.question and bool(r.unavailable) for r in selected),
            "model_asked": sum(bool(r.decisions) for r in selected),
            **_question_sources(selected),
            "unavailable_causes": {
                name: causes[name] for name in (*_CAUSES, "other") if causes[name]
            },
            "unprobed": sum(bool(r.unprobed) for r in selected),
            "observed_difference": sum(r.detected is True for r in selected),
            "not_collected": sum(r.detected is None for r in selected),
        }
    missed = groups["unasked_difference"]
    needless = groups["only_asked"]
    return {
        "complete": complete,
        "criteria_passed": complete and missed["let_through"] == 0 and needless["questions"] <= 2,
        "criteria": [
            f"Unasked differences let through: {missed['let_through']}/10; criterion 0; "
            f"unavailable questions {missed['unavailable_questions']}/10"
            + _question_suffix(missed),
            f"Only-asked needless questions: {needless['questions']}/10; criterion <=2; "
            f"unavailable questions {needless['unavailable_questions']}/10"
            + _question_suffix(needless),
        ],
        "detection": {
            "groups": {
                name: {
                    k: v
                    for k, v in group.items()
                    if k
                    in {
                        "denominator",
                        "executed",
                        "observed_difference",
                        "unprobed",
                        "not_collected",
                    }
                }
                for name, group in groups.items()
            },
            "meaning": (
                "Any observed difference; this does not establish detection of the labeled "
                "unasked difference."
            ),
        },
        "classification": {
            "groups": {
                name: {
                    k: v
                    for k, v in group.items()
                    if k not in {"observed_difference", "not_collected"}
                }
                for name, group in groups.items()
            },
            "choices": {
                choice: sum(d.choice == choice for r in rows for d in r.decisions)
                for choice in ("asked_for", "not_asked_for", "cannot_tell")
            },
        },
        "accounting": metrics(records),
        "attempts": [r.model_dump(mode="json") for r in records],
    }


def summarize(
    records: list[Record],
    answers: dict[str, str],
    *,
    expected_repetitions: int,
    labels_hash: str | None = None,
) -> dict:
    if expected_repetitions < 1:
        raise BenchError("positive repetition count required")
    selected = [r for r in records if r.arm_id in {a.id for a in ARMS}]
    for record in selected:
        if (
            not record.pilot
            and record.result
            and record.result.detection
            and record.result.detection.labels_sha256 != labels_hash
        ):
            raise BenchError("report labels pin differs from recorded scoring inputs")
    arms = {}
    for arm in ARMS:
        rows = [r for r in selected if r.arm_id == arm.id]
        bench = [r for r in rows if not r.pilot]
        repetitions_count = max(expected_repetitions, max((r.repetition for r in bench), default=1))
        repetitions = {
            str(rep): repetition([r for r in bench if r.repetition == rep], answers)
            for rep in range(1, 1 + (1 if arm.profile == "none" else repetitions_count))
        }
        arms[arm.id] = {
            "pilots": metrics([r for r in rows if r.pilot]),
            "pilot_attempts": [r.model_dump(mode="json") for r in rows if r.pilot],
            "including_pilots": metrics(rows),
            "repetitions": repetitions,
        }
    all_results = [rep for arm in arms.values() for rep in arm["repetitions"].values()]
    return {
        "protocol": PROTOCOL,
        "calibration": "not available",
        "arms": arms,
        "superseded_pilots": superseded_pilots(records),
        "complete": all(r["complete"] for r in all_results),
        "criteria_passed": all(r["criteria_passed"] for r in all_results),
        "shared_accounting_including_history": metrics(records),
        "known_charges_usd": sum(r.charged_usd for r in records),
        "shared_unknown_costs": [r.attempt_id for r in records if r.unknown],
        "shared_reservations": [
            {
                "attempt_id": r.attempt_id,
                "arm_id": r.arm_id,
                "allowance_usd": r.allowance_usd,
                "charged_usd": r.charged_usd,
            }
            for r in records
            if r.status != "finished"
        ],
    }


def superseded_pilots(records: list[Record]) -> list[dict]:
    return [
        {
            "arm_id": r.arm_id,
            "attempt_id": r.attempt_id,
            "status": "superseded",
            "charged_usd": r.charged_usd,
            "cost_usd": r.result.cost_usd if r.result else "unknown",
        }
        for r in records
        if r.pilot and r.arm_id in SUPERSEDED_ARMS
    ]


def _fake_cases(records: list[Record]) -> tuple[str, dict]:
    cases, choices = {}, set()
    for record in records:
        evidence = record.result.detection if record.result else None
        if record.pilot or evidence is None or record.arm_id not in {a.id for a in ARMS}:
            continue
        config = evidence.configuration
        if config.get("fake") is not True or config.get("fake_choice") not in {
            "asked_for",
            "not_asked_for",
            "cannot_tell",
            "unavailable",
        }:
            raise BenchError("sensitivity requires two recorded fake runs")
        choices.add(config["fake_choice"])
        if evidence.patch_id not in {f"b{i:02}" for i in range(1, 21)}:
            continue
        key = (record.arm_id, record.repetition, evidence.patch_id)
        if key in cases:
            raise BenchError(f"duplicate fake case: {key}")
        cases[key] = evidence
    if len(choices) != 1:
        raise BenchError("each fake ledger must record exactly one fake choice")
    return next(iter(choices)), cases


def _case_count(ids) -> dict:
    ordered = sorted(ids)
    return {"count": len(ordered), "ids": ordered}


def sensitivity(first: list[Record], second: list[Record], answers: dict[str, str]) -> dict:
    """Compare recorded fake choices by case identity, without I/O or model calls."""
    first_choice, left = _fake_cases(first)
    second_choice, right = _fake_cases(second)
    if first_choice == second_choice:
        raise BenchError("sensitivity requires different fake choices")
    arms = {}
    for arm in ARMS:
        repetitions = {}
        reps = {rep for aid, rep, _ in left.keys() | right.keys() if aid == arm.id} or {1}
        for rep in sorted(reps):
            passes = {
                name: {
                    pid: row for (aid, r, pid), row in cases.items() if (aid, r) == (arm.id, rep)
                }
                for name, cases in (("first", left), ("second", right))
            }
            a, b = passes.values()
            paired = a.keys() & b.keys()
            flips = {pid for pid in paired if a[pid].let_through != b[pid].let_through}
            repetitions[str(rep)] = {
                "groups": {
                    answer: {
                        "paired": sum(answers.get(pid) == answer for pid in paired),
                        "model_sensitive": sum(answers.get(pid) == answer for pid in flips),
                        "unpaired": sorted(
                            pid for pid in a.keys() ^ b.keys() if answers.get(pid) == answer
                        ),
                    }
                    for answer in ("unasked_difference", "only_asked")
                },
                "passes": {
                    name: {
                        "cases": _case_count(rows),
                        "flips": _case_count(flips),
                        "evidence_alone": _case_count(paired - flips),
                        "unavailable_questions": _case_count(
                            pid for pid, row in rows.items() if row.question and row.unavailable
                        ),
                    }
                    for name, rows in passes.items()
                },
                "unasked_passing_both_not_flipped": sorted(
                    pid
                    for pid in paired - flips
                    if answers.get(pid) == "unasked_difference"
                    and a[pid].tests_passed
                    and b[pid].tests_passed
                ),
            }
        arms[arm.id] = {"repetitions": repetitions}
    return {"fake_choices": {"first": first_choice, "second": second_choice}, "arms": arms}


def paid_run_gate(result: dict) -> dict:
    """Apply the maintainer's threshold fixed on 2026-10-30; authorize no execution."""
    failures = []
    expected = {f"b{i:02}" for i in range(1, 21)}
    for arm in (a for a in ARMS if a.behavior):
        for rep, report in result["arms"][arm.id]["repetitions"].items():
            prefix = f"{arm.id} repetition {rep}"
            counts = []
            for name, run in report["passes"].items():
                if set(run["cases"]["ids"]) != expected:
                    failures.append(f"{prefix} {name}: requires all 20 labelled cases b01-b20")
                count = run["unavailable_questions"]["count"]
                counts.append(count)
                if count > 4:
                    failures.append(f"{prefix} {name}: question not available {count}/20 exceeds 4")
            if len(set(counts)) != 1:
                failures.append(f"{prefix}: question not available counts differ: {counts}")
            missed = report["unasked_passing_both_not_flipped"]
            if missed:
                failures.append(
                    f"{prefix}: unasked cases with passing tests did not flip: {missed}"
                )
    return {"paid_run_allowed": not failures, "failed_conditions": failures}
