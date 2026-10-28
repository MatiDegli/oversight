"""Internal notification boundary between confirmed proposal facts and runtime review."""

from dataclasses import dataclass
from typing import Literal, Protocol

from oversight.workspace import Tree
from oversight_contracts.events import ChangeProposed
from oversight_contracts.review import ChangeTarget


@dataclass(frozen=True)
class ReviewCapture:
    """Main is context, not freshness: approving A must preserve B's review."""

    target: ChangeTarget
    tree: Tree


class ReviewPort(Protocol):
    def facts_ready(
        self,
        capture: ReviewCapture,
        proposal: ChangeProposed,
        *,
        author: Literal["agent", "user"],
    ) -> None:
        """Facts were confirmed without interruption; the author run may still be working."""
        ...
