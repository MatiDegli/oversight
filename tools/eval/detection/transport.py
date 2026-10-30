"""Durable send counting outside the frozen diagnostic and prompt fingerprints."""

from openai.types.chat import ChatCompletion
from tools.eval.detection.accounting import Budget
from tools.eval.detection.diagnostics import DetectionClient

from oversight.agents import DecisionUnavailable
from oversight.agents.client import CallScope


class TransportClient(DetectionClient):
    def __init__(self, *args, budget: Budget | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.budget = budget

    async def _decision_attempt(
        self, params: dict[str, object], scope: CallScope
    ) -> ChatCompletion:
        if self.budget is None:
            raise DecisionUnavailable("budget_unconfigured")
        return await super()._decision_attempt(params, scope)

    async def _completion(self, params: dict[str, object]) -> ChatCompletion:
        if self.budget is None:
            raise DecisionUnavailable("budget_unconfigured")
        self.budget.sent()  # Synchronous durability before the first transport await.
        return await super()._completion(params)
