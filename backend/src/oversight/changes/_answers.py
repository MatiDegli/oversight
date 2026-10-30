"""Pure admission of human answers against the server's published questions."""

from collections.abc import Sequence

from oversight.changes._state import WrongState
from oversight_contracts.review import ReviewCard


class InvalidAnswers(ValueError):
    """An answer does not identify one current card: HTTP invalid_request."""


def validate_answers(
    cards: Sequence[ReviewCard], answers: Sequence[ReviewCard] | None
) -> list[ReviewCard] | None:
    """Rebuild answers from server cards, in publication order, without mutating either input."""
    if answers is None:
        return None
    current = {(card.node_id, card.question): card for card in cards}
    if len(current) != len(cards):
        raise InvalidAnswers("Server question identity is duplicated")
    accepted: dict[tuple[str, str], ReviewCard] = {}
    for answer in answers:
        identity = answer.node_id, answer.question
        card = current.get(identity)
        if (
            card is None
            or identity in accepted
            or (answer.evidence_seq, answer.example) != (card.evidence_seq, card.example)
            or answer.answer not in {"yes", "no", "unsure"}
            or answer.answered_by != "person"
        ):
            raise InvalidAnswers(f"Invalid answer for {answer.node_id}: {answer.question}")
        accepted[identity] = card.model_copy(
            deep=True, update={"answer": answer.answer, "answered_by": "person"}
        )
    return [accepted[key] for card in cards if (key := (card.node_id, card.question)) in accepted]


def require_yes(
    cards: Sequence[ReviewCard], answers: Sequence[ReviewCard] | None, *, pending: bool
) -> None:
    if pending:
        raise WrongState("questions are still being prepared")
    yes = {(card.node_id, card.question) for card in answers or () if card.answer == "yes"}
    open_questions = [
        f"{card.node_id}: {card.question}"
        for card in cards
        if (card.node_id, card.question) not in yes
    ]
    if open_questions:
        raise WrongState(
            "Approve needs Yes for every current question: " + "; ".join(open_questions)
        )
