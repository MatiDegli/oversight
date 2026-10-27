"""Pure observed-target checks; owners run them before reservation and before effects."""

from oversight.changes._state import ChangeRecord, State, WrongState


def validate_decision_target(
    rec: ChangeRecord,
    revision: int,
    head_commit: str,
    allowed: frozenset[State],
    *,
    reserved: bool = False,
) -> None:
    """A stale request or terminal delivery failure must not reach an effect."""
    state = rec.fallback if reserved and rec.state == "deciding" else rec.state
    if rec.delivery_rejected or state not in allowed:
        raise WrongState(
            f"{rec.change_id} is {rec.state}; needs {' or '.join(sorted(allowed))}; "
            "refresh and review before deciding"
        )
    if (revision, head_commit) != (rec.revision, rec.head_commit or rec.base_commit):
        raise WrongState(f"{rec.change_id} target changed; refresh and review before deciding")
