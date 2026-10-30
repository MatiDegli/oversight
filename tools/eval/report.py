"""Per-arm outcomes with individual repetitions and unknown-aware totals."""

import json
from collections import defaultdict

from tools.eval.grade import Count
from tools.eval.ledger import Ledger, Record


def total(values: list) -> Count | float:
    return "unknown" if "unknown" in values else sum(values)


def metrics(records: list[Record]) -> dict:
    rows = [r.result for r in records if r.result]
    incomplete = any(r.unknown or r.status != "finished" for r in records)
    return {
        "attempts": len(rows),
        "tasks_solved": sum(r.outcome == "solved" for r in rows),
        "turns": total([r.turns for r in rows]),
        "steps": sum(r.steps for r in rows),
        "tokens": {
            "input": "unknown" if incomplete else total([r.tokens.input for r in rows]),
            "output": "unknown" if incomplete else total([r.tokens.output for r in rows]),
        },
        "cost_usd": "unknown" if incomplete else total([r.cost_usd for r in rows]),
        "wall_time_s": sum(r.duration_s for r in rows),
    }


def failure_counts(ledger: Ledger, record: Record) -> dict:
    path = ledger.out.parent / f"events-{record.run_id}.jsonl"
    if not path.is_file() or record.agent_id is None:
        return {"evidence": "not available"}
    counts: dict = {
        "tool_calls": {},
        "edit_file_errors": 0,
        "run_tests_failed": 0,
        "finish_with_passing_tests": 0,
        "finish_without_passing_tests": 0,
        "terminal_status": None,
    }
    passed = False
    for line in path.read_text().splitlines():
        stored = json.loads(line)
        event = stored["event"]
        payload = event["payload"]
        if stored["run_id"] != record.run_id or payload.get("agent_id") != record.agent_id:
            continue
        if event["type"] == "agent.status":
            counts["terminal_status"] = {"state": payload["state"], "detail": payload["detail"]}
        elif event["type"] == "agent.step":
            tool, result = payload["tool"], payload["result"]
            by_result = counts["tool_calls"].setdefault(tool, {"ok": 0, "error": 0})
            by_result[result] += 1
            counts["edit_file_errors"] += tool == "edit_file" and result == "error"
            if tool == "run_tests":
                tests = payload["tests"]
                counts["run_tests_failed"] += result == "error" or bool(
                    tests and tests["outcome"] != "passed"
                )
                passed |= bool(result == "ok" and tests and tests["outcome"] == "passed")
            if tool == "finish":
                counts[
                    "finish_with_passing_tests" if passed else "finish_without_passing_tests"
                ] += 1
    return counts


def failure_table(ledger: Ledger) -> dict:
    configurations = {
        a["arm_id"]: a for p in ledger.state.plans.values() for a in p.get("arms", [])
    }
    manifest = ledger.out.parent / "manifest.json"
    if manifest.is_file():
        for entry in json.loads(manifest.read_text())["runs"].values():
            experiment = entry.get("experiment")
            if experiment:
                configurations.setdefault(experiment["arm_id"], experiment["configuration"])
    table = {}
    records = ledger.state.records
    for arm_id in {r.arm_id for r in records}:
        config = configurations.get(arm_id, {})
        counterparts = {
            name
            for name, other in configurations.items()
            if config.get("harness_pack") in {"on", "off"}
            and other.get("harness_pack") != config["harness_pack"]
            and all(config.get(f) == other.get(f) for f in ("profile", "reasoning", "tariff"))
        }
        table[arm_id] = {}
        for task_id in sorted({r.task_id for r in records}):
            cells = [r for r in records if r.arm_id == arm_id and r.task_id == task_id]
            table[arm_id][task_id] = [
                {
                    "attempt_id": r.attempt_id,
                    "repetition": r.repetition,
                    "pairing": "paired"
                    if any(
                        other.arm_id in counterparts
                        and other.task_id == task_id
                        and other.task_hash == r.task_hash
                        and other.repetition == r.repetition
                        and other.agent_id is not None
                        for other in records
                    )
                    else "unpaired",
                    **failure_counts(ledger, r),
                }
                for r in cells
            ] or "unpaired"
    return {"measurement": "error kind: not measured", "arms": table}


def report(ledger: Ledger) -> dict:
    groups: dict[str, list[Record]] = defaultdict(list)
    for record in ledger.state.records:
        groups[record.arm_id].append(record)
    arms = {}
    for name, records in groups.items():
        rows = [r.result for r in records if r.result]
        repetitions = {
            str(rep): metrics([r for r in records if r.repetition == rep])
            for rep in sorted({r.repetition for r in records})
        }
        aggregate = metrics(records)
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
        "failure_table": failure_table(ledger),
        "known_charges_usd": ledger.spent,
        "cost_usd": "unknown"
        if any(r.unknown or r.status != "finished" for r in ledger.state.records)
        else ledger.spent,
        "unsettled_reservations": sum(r.status != "finished" for r in ledger.state.records),
    }
