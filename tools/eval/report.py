"""Per-arm outcomes with individual repetitions and unknown-aware totals."""

from collections import defaultdict

from tools.eval.grade import Count, Result
from tools.eval.ledger import Ledger, Record


def total(values: list) -> Count | float:
    return "unknown" if "unknown" in values else sum(values)


def metrics(rows: list[Result]) -> dict:
    return {
        "attempts": len(rows),
        "tasks_solved": sum(r.outcome == "solved" for r in rows),
        "turns": sum(r.turns for r in rows),
        "steps": sum(r.steps for r in rows),
        "tokens": {
            "input": total([r.tokens.input for r in rows]),
            "output": total([r.tokens.output for r in rows]),
        },
        "cost_usd": total([r.cost_usd for r in rows]),
        "wall_time_s": sum(r.duration_s for r in rows),
    }


def report(ledger: Ledger) -> dict:
    groups: dict[str, list[Record]] = defaultdict(list)
    for record in ledger.state.records:
        groups[record.arm_id].append(record)
    arms = {}
    for name, records in groups.items():
        rows = [r.result for r in records if r.result]
        repetitions = {
            str(rep): metrics([r for r in rows if r.repetition == rep])
            for rep in sorted({r.repetition for r in rows})
        }
        aggregate = metrics(rows)
        if any(r.unknown or r.status != "finished" for r in records):
            aggregate["cost_usd"] = "unknown"
        arms[name] = {
            "aggregate": aggregate,
            "repetitions": repetitions,
            "mean_tasks_solved": sum(r.outcome == "solved" for r in rows)
            / max(1, len(repetitions)),
            "attempts": [r.model_dump(mode="json") for r in rows],
            "reservations": [
                {
                    "attempt_id": r.attempt_id,
                    "repetition": r.repetition,
                    "allowance_usd": r.allowance_usd,
                    "charged_usd": r.charged_usd,
                }
                for r in records
                if r.status != "finished"
            ],
        }
    return {
        "arms": arms,
        "known_charges_usd": ledger.spent,
        "cost_usd": "unknown"
        if any(r.unknown or r.status != "finished" for r in ledger.state.records)
        else ledger.spent,
        "unsettled_reservations": sum(r.status != "finished" for r in ledger.state.records),
    }
