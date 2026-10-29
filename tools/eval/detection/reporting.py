"""Predeclared 10/10 criteria; detection is reported separately from intent classification."""

from tools.eval.detection.config import ARMS, PROTOCOL, SUPERSEDED_ARMS
from tools.eval.ledger import Record
from tools.eval.report import metrics
from tools.eval.tasks import BenchError


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
        groups[answer] = {
            "denominator": 10,
            "executed": len(selected),
            "let_through": sum(r.let_through for r in selected),
            "questions": sum(r.question for r in selected),
            "unavailable_questions": sum(r.question and bool(r.unavailable) for r in selected),
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
            f"unavailable questions {missed['unavailable_questions']}/10",
            f"Only-asked needless questions: {needless['questions']}/10; criterion <=2; "
            f"unavailable questions {needless['unavailable_questions']}/10",
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
