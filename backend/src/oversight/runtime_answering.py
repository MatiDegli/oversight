"""Live-only admission of historical decision links; replay never calls this module."""

from oversight.agents import UNAVAILABLE_PRODUCERS, AnsweringError, validate_answering
from oversight.api import ApiError
from oversight.events import EventStore
from oversight_contracts.decisions import DecisionRef


def admit(reference: DecisionRef | None, store: EventStore) -> None:
    """Only the terminal-check producer exists today. No production override opens the others."""
    if reference is None:
        return
    if reference.kind in UNAVAILABLE_PRODUCERS:
        raise ApiError("wrong_state", UNAVAILABLE_PRODUCERS[reference.kind])
    try:
        validate_answering(reference, store.committed_events(), run_id=store.run_id)
    except AnsweringError as error:
        raise ApiError(error.code, str(error)) from None
