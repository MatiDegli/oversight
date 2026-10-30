"""Durable send counting outside the frozen diagnostic and prompt fingerprints."""

from dataclasses import replace

from openai.types.chat import ChatCompletion
from tools.eval.detection.accounting import Budget
from tools.eval.detection.diagnostics import DetectionClient
from tools.eval.tasks import BenchError

from oversight.agents import DecisionUnavailable
from oversight.agents.client import CallScope


class TransportClient(DetectionClient):
    def __init__(self, *args, budget: Budget | None = None, **kwargs) -> None:
        self.budget = budget
        self._transport_entered = False
        super().__init__(*args, **kwargs)

    @property
    def budget(self) -> Budget | None:
        return self._budget

    @budget.setter
    def budget(self, budget: Budget | None) -> None:
        if budget is not None and budget.fake:
            raise BenchError("transport requires a nonfake budget")
        self._budget = budget

    async def _decision_attempt(
        self, params: dict[str, object], scope: CallScope
    ) -> ChatCompletion:
        if self.budget is None:
            raise DecisionUnavailable("budget_unconfigured")
        budget = self.budget
        admission = scope.admission
        if admission is None:
            return await super()._decision_attempt(params, scope)
        counted = False
        self._transport_entered = False

        async def admit_and_count() -> None:
            nonlocal counted
            await admission()
            counted = True
            budget.sent()  # Persist before the product's final cancellation/deadline guards.

        try:
            return await super()._decision_attempt(
                params, replace(scope, admission=admit_and_count)
            )
        except BaseException:
            if counted and not self._transport_entered:
                budget.unsent()
            raise

    async def _completion(self, params: dict[str, object]) -> ChatCompletion:
        if self.budget is None:
            raise DecisionUnavailable("budget_unconfigured")
        self._transport_entered = True
        return await super()._completion(params)
