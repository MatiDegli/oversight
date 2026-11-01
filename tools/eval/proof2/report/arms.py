"""Revision correctness, hidden regressions and the two fixed selections."""

from .inputs import Inputs, change_number, hidden_status
from .output import counted


def failing_ids(result: dict | None) -> set[str] | None:
    if result is None:
        return None
    failed, errors, ids = (result.get(k) for k in ("failed", "errors", "failing_test_ids"))
    if (
        type(failed) is int
        and type(errors) is int
        and isinstance(ids, list)
        and all(isinstance(item, str) for item in ids)
        and failed + errors == len(ids)
    ):
        return set(ids)
    return None


def regression(current, reference, applicable=True) -> dict:
    if not applicable:
        return dict(status="not_applicable", ids=[])
    now, before = failing_ids(current), failing_ids(reference)
    if now is None or before is None:
        return dict(status="unknown", ids=[])
    return dict(status="known", ids=sorted(now - before))


def receipts(inputs: Inputs):
    green, merged, reviews, requests = {}, {}, {}, {}
    problems = []
    for event in inputs.events:
        if event.type == "change.proposed" and event.payload.tests.outcome == "passed":
            green.setdefault(event.payload.change_id, event.payload.revision)
        elif event.type == "change.reviewed":
            key = (event.payload.change_id, event.payload.revision)
            reviews.setdefault(key, []).append(event.payload.verdict)
        elif event.type == "change.resolved":
            p = event.payload
            if p.decision == "changes_requested":
                requests[p.change_id, p.revision] = p.decided_by or "unrecorded"
            elif p.decision == "approved" and p.merge == "merged":
                if p.change_id in merged:
                    problems.append(f"multiple merged resolutions for {p.change_id}")
                merged[p.change_id] = p
    return green, merged, reviews, requests, problems


def revision_records(inputs: Inputs, green, merged, reviews, requests) -> list[dict]:
    revisions = []
    for key, event in sorted(
        inputs.proposals.items(), key=lambda item: (change_number(item[0][0]), item[0][1])
    ):
        cid, revision = key
        p = event.payload
        task = inputs.tasks.get(cid)
        tests_only = inputs.kinds.get(task or "") == "tests"
        hidden = hidden_status(inputs.results.get(key))
        correct = (hidden == "pass") if hidden != "unknown" else None
        if not tests_only and p.tests.outcome != "passed":
            correct = False
        previous = max((r for c, r in inputs.results if c == cid and r < revision), default=None)
        revisions.append(
            dict(
                record="revision",
                change_id=cid,
                task=task,
                revision=revision,
                previous=previous,
                seq=event.seq,
                head_commit=p.head_commit,
                tests=p.tests.outcome,
                hidden=hidden,
                correct=correct,
                first_green=green.get(cid) == revision,
                merged=cid in merged and merged[cid].revision == revision,
                verdicts=reviews.get(key, []),
                request=requests.get(key),
                regress_vs_base=regression(
                    inputs.results.get(key), inputs.bases.get(task or ""), not tests_only
                ),
                regress_vs_previous=regression(
                    inputs.results.get(key),
                    inputs.results.get((cid, previous)) if previous is not None else None,
                    not tests_only and previous is not None,
                ),
            )
        )
    return revisions


def build_arms(inputs: Inputs) -> tuple[list[dict], list[dict], list[dict]]:
    green, merged, reviews, requests, problems = receipts(inputs)
    revisions = revision_records(inputs, green, merged, reviews, requests)
    changes = [
        dict(
            record="change",
            change_id=c.change_id,
            agent_id=c.agent_id,
            task=inputs.tasks.get(c.change_id),
            proposals=c.proposals,
            rounds=c.review_rounds,
            end=c.end,
            first_green=green.get(c.change_id),
            merged_revision=merged[c.change_id].revision if c.change_id in merged else None,
            merged_by=merged[c.change_id].decided_by if c.change_id in merged else None,
        )
        for c in sorted(inputs.supervision.changes, key=lambda c: change_number(c.change_id))
    ]
    arms = []
    for name, selection in (
        ("without_oversight", green),
        ("with_oversight", {cid: p.revision for cid, p in merged.items()}),
    ):
        selected = [r for r in revisions if selection.get(r["change_id"]) == r["revision"]]
        regressions = [r["regress_vs_base"] for r in selected]
        arms.append(
            dict(
                record="arm",
                arm=name,
                changes=len(changes),
                correct=counted(sum(r["correct"] is True for r in selected), len(changes)),
                incorrect=sum(r["correct"] is False for r in selected),
                unknown=sum(r["correct"] is None for r in selected),
                not_selected=len(changes) - len(selected),
                regressions_let_through=dict(
                    changes=sum(r["status"] == "known" and bool(r["ids"]) for r in regressions),
                    checks=sum(len(r["ids"]) for r in regressions),
                    unknown_changes=sum(r["status"] == "unknown" for r in regressions),
                ),
                questions=dict(status="not_built"),
                questions_not_needed=dict(status="not_built"),
                problems=sorted(set(problems)),
            )
        )
    return revisions, changes, arms
