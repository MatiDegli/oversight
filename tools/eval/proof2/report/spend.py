"""List-price costs over receipt-order rounds; ledger dollars are never prices."""

from collections import Counter

from oversight.events import price_usage


def priced(events):
    return price_usage([event.model_copy(update={"seq": i}) for i, event in enumerate(events, 1)])


def cost(events) -> dict:
    fold = priced(events)
    return dict(calls=fold.calls, usd=fold.usd, known_usd=fold.known_usd, coverage=fold.coverage)


def add_spend(inputs, changes, arms) -> None:
    ticks, without = [], []
    rounds = Counter()
    green = {c["change_id"]: c["first_green"] for c in changes}
    for event in inputs.events:
        if event.type == "change.resolved" and event.payload.decision == "changes_requested":
            rounds[event.payload.change_id] += 1
        elif event.type == "usage.tick":
            ticks.append(event)
            tick = event.payload
            limit = green.get(tick.change_id)
            if tick.role == "agent" and (
                tick.change_id is None or limit is None or rounds[tick.change_id] <= limit - 1
            ):
                without.append(event)
    for change in changes:
        change["review_cost"] = cost(
            [
                e
                for e in ticks
                if e.payload.role == "review" and e.payload.change_id == change["change_id"]
            ]
        )
    for arm, selected in zip(arms, (without, ticks), strict=True):
        arm["cost"] = cost(selected)
        roles = {e.payload.role or "unknown_role" for e in selected}
        if arm["arm"] == "without_oversight":
            roles = {"agent"}
        arm["by_role"] = {
            role: cost([e for e in selected if (e.payload.role or "unknown_role") == role])
            for role in sorted(roles)
        }
        problems = [*arm["problems"], *priced(selected).problems]
        if arm["arm"] == "without_oversight":
            missing_role = sum(e.payload.role is None for e in ticks)
            missing_change = sum(
                e.payload.role == "agent" and e.payload.change_id is None for e in ticks
            )
            if missing_role:
                arm["cost"].update(usd=None, coverage="missing_metrics")
                problems.append(f"{missing_role} ticks without a role")
            if missing_change:
                problems.append(f"{missing_change} agent ticks without a change")
        total, correct = arm["cost"]["usd"], arm["correct"]["count"]
        arm["usd_per_correct"] = total / correct if total is not None and correct else None
        arm["problems"] = sorted(set(problems))
