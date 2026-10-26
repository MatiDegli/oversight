"""Usage and spend: the cost of a call when it can be known, and the run's ledger and brake."""

from dataclasses import dataclass
from typing import Literal

from oversight.agents.client import Usage

type Brake = Literal["ok", "warn_50", "warn_80", "blocked"]


@dataclass(frozen=True)
class Pricing:
    """US dollars per million tokens."""

    input_usd: float
    output_usd: float
    cached_input_usd: float

    def cost(self, usage: Usage) -> float | None:
        """`None` when a count the price needs was not reported: unknown, never free."""
        if usage.input_tokens is None or usage.output_tokens is None:
            return None
        # Unreported cached tokens price as fresh input: an upper bound, never an undercount.
        cached = usage.cached_tokens or 0
        fresh = max(usage.input_tokens - cached, 0)
        total = (
            fresh * self.input_usd
            + cached * self.cached_input_usd
            + usage.output_tokens * self.output_usd
        )
        return total / 1_000_000


class UsageLedger:
    """Spend across the agents of a run. A call of unknown cost adds nothing it cannot know."""

    def __init__(
        self,
        budget_usd: float,
        spent_usd: float = 0.0,
        *,
        warn_at: tuple[float, float] = (0.5, 0.8),
        block_at: float = 0.9,
    ) -> None:
        self.budget_usd = budget_usd
        self.spent_usd = spent_usd
        self._warn_at = warn_at
        self._block_at = block_at

    def record(self, cost_usd: float | None) -> float:
        if cost_usd is not None:
            self.spent_usd += cost_usd
        return self.spent_usd

    @property
    def brake(self) -> Brake:
        if self.budget_usd <= 0:
            return "blocked"
        share = self.spent_usd / self.budget_usd
        if share > self._block_at:
            return "blocked"
        if share >= self._warn_at[1]:
            return "warn_80"
        if share >= self._warn_at[0]:
            return "warn_50"
        return "ok"
