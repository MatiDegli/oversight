"""Cross-review agreement and revision requests, preserving missing evidence."""

from collections import Counter

from .arms import RevisionRecord
from .inputs import Inputs, hidden_status
from .output import counted
from .spend import cost


def measures(inputs: Inputs, revisions: list[RevisionRecord]) -> list[dict[str, object]]:
    reviews, duplicates = {}, 0
    requests = []
    review_ticks = []
    for event in inputs.events:
        if event.type == "change.reviewed":
            review = event.payload
            key = (review.change_id, review.revision)
            if key in reviews:
                duplicates += 1
            else:
                reviews[key] = review.verdict
        elif event.type == "change.resolved" and event.payload.decision == "changes_requested":
            requests.append(event.payload)
        elif event.type == "usage.tick" and event.payload.role == "review":
            review_ticks.append(event)
    cells = Counter(
        (verdict, hidden_status(inputs.results.get(key))) for key, verdict in reviews.items()
    )
    e1: dict[str, object] = dict(
        record="E1",
        n=len(reviews),
        cells=[
            dict(
                verdict=verdict,
                **{
                    f"hidden_{hidden}": counted(cells[verdict, hidden], len(reviews))
                    for hidden in ("pass", "fail", "unknown")
                },
            )
            for verdict in ("clean", "changes_requested", "unknown")
        ],
        duplicate_reviews=duplicates,
        review_cost=cost(review_ticks),
    )
    by_round = Counter((r["revision"] - 1, r["hidden"]) for r in revisions)
    e2: dict[str, object] = dict(
        record="E2",
        changes_with_requests=len({p.change_id for p in requests}),
        rounds=len(requests),
        requests_by={
            actor: sum((p.decided_by or "unrecorded") == actor for p in requests)
            for actor in ("person", "rules", "cross_review", "lead_agent", "unrecorded")
        },
        hidden_by_round=[
            {"round": k, **{h: by_round[k, h] for h in ("pass", "fail", "unknown")}}
            for k in sorted({r["revision"] - 1 for r in revisions})
        ],
        findings=dict(status="not_built"),
    )
    for name in ("regress_vs_base", "regress_vs_previous"):
        e2[name] = dict(
            checks=sum(len(r[name]["ids"]) for r in revisions if r[name]["status"] == "known"),
            unknown_revisions=sum(r[name]["status"] == "unknown" for r in revisions),
        )
    return [e1, e2]
