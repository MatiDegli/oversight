"""D6 model adapters; provider clients are injected, never selected through settings."""

import asyncio
import json
from typing import cast

import openai
from tools.eval.detection.accounting import Budget
from tools.eval.detection.config import NO_BEHAVIOR, Arm, system_prompt
from tools.eval.detection.diagnostics import CURRENT_TRACE, Trace
from tools.eval.detection.jsonio import unique
from tools.eval.detection_types import CallDiagnostics, DecisionEvidence, ValidationStage
from tools.eval.tasks import BenchError

from oversight.agents import (
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    DecisionClient,
    DecisionInput,
    DecisionMissing,
    DecisionOutcome,
    DecisionSelected,
    DecisionUnavailable,
    ModelAPIError,
    ModelReply,
    Usage,
    classify_difference,
)
from oversight.agents.client import CALL_SCOPE, CallScope, EmptyChoicesError
from oversight.agents.decision import CHOICES
from oversight_contracts.review import ChangeTarget


def messages(
    task: str,
    *,
    input: DecisionInput | None = None,
    diff: str | None = None,
    target: ChangeTarget | None = None,
) -> list[dict]:
    if input is not None:
        payload = {
            "target": input.target.model_dump(),
            "task": input.task,
            "difference": input.difference.model_dump(),
            "confirmed_examples": [e.model_dump() for e in input.confirmed_examples],
        }
    else:
        if diff is None or target is None:
            raise BenchError("task-and-diff arm needs diff and target")
        payload = {
            "target": target.model_dump(),
            "task": task,
            "diff": diff,
            "behavior_evidence": NO_BEHAVIOR,
        }
    return [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class Adapter:
    def __init__(
        self,
        arm: Arm,
        budget: Budget,
        *,
        client: DecisionClient | None = None,
        fake_choice: str | None = None,
    ) -> None:
        if arm.profile != "none" and client is None and fake_choice is None:
            raise BenchError(f"{arm.id}: unavailable model adapter; explicit client required")
        if fake_choice is not None and fake_choice not in {*CHOICES, "unavailable"}:
            raise BenchError("invalid explicit fake outcome")
        if client is not None:
            expected = MUSE_SPARK_OPEN if arm.profile == "baseten" else MUSE_SPARK
            if client.profile != expected:
                raise BenchError("adapter profile mismatch")
        self.arm, self.budget, self.client, self.fake_choice = arm, budget, client, fake_choice
        self.evidence: list[DecisionEvidence] = []

    async def _fake(self, target: ChangeTarget, node: str) -> DecisionOutcome:
        await self.budget.admission()
        await self.budget.accounting(ModelReply(None, [], Usage(0, 0, 0), "explicit-fake"))
        if self.fake_choice == "unavailable":
            return DecisionMissing(target, node, "transport")
        return DecisionSelected(target, node, self.fake_choice)  # type: ignore[arg-type]

    async def classify_difference(
        self, input: DecisionInput, *, deadline: float
    ) -> DecisionOutcome:
        outcome, diagnostic = await self.decide(
            messages(input.task, input=input),
            input.target,
            input.difference.node_id,
            deadline,
            input,
        )
        self.evidence.append(
            DecisionEvidence(
                target=input.target,
                node_id=input.difference.node_id,
                task=input.task,
                difference=input.difference,
                choice=outcome.choice,
                unavailable=outcome.unavailable,
                diagnostics=diagnostic,
            )
        )
        return outcome

    async def decide(
        self,
        prompt: list[dict],
        target: ChangeTarget,
        node: str,
        deadline: float,
        input: DecisionInput | None = None,
    ) -> tuple[DecisionOutcome, CallDiagnostics]:
        trace = Trace(
            MUSE_SPARK_OPEN.model if self.arm.role == "behavior-Spark-Open" else MUSE_SPARK.model
        )
        token = CURRENT_TRACE.set(trace)
        started = asyncio.get_running_loop().time()
        try:
            outcome = await self._decide(prompt, target, node, deadline, input)
        finally:
            trace.diagnostic.latency_s = asyncio.get_running_loop().time() - started
            CURRENT_TRACE.reset(token)
        if outcome.unavailable and outcome.unavailable != "invalid_response":
            trace.diagnostic.validation_stage = cast(ValidationStage, outcome.unavailable)
        return outcome, trace.diagnostic

    async def _decide(
        self,
        prompt: list[dict],
        target: ChangeTarget,
        node: str,
        deadline: float,
        input: DecisionInput | None,
    ) -> DecisionOutcome:
        if self.fake_choice is not None:
            return await self._fake(target, node)
        if self.arm.role == "behavior-Spark-Open":
            assert input is not None
            return await classify_difference(
                self.observed_client(),
                input,
                deadline=deadline,
                admission=self.budget.admission,
                accounting=self.budget.accounting,
            )
        return await self.spark(prompt, target, node, deadline)

    def observed_client(self) -> DecisionClient:
        client = self.client
        assert client is not None

        class Observed:
            profile = client.profile

            async def complete(self, messages, tools):
                try:
                    reply = await client.complete(messages, tools)
                except ModelAPIError as error:
                    scope = CALL_SCOPE.get()
                    if (
                        isinstance(error.__cause__, openai.APITimeoutError)
                        or (
                            scope is not None
                            and scope.deadline is not None
                            and asyncio.get_running_loop().time() >= scope.deadline
                        )
                        or "exceeds the task deadline" in str(error)
                    ):
                        raise TimeoutError from error
                    if isinstance(error, EmptyChoicesError):
                        trace = CURRENT_TRACE.get()
                        assert trace is not None
                        trace.reply(error.reply)
                        trace.diagnostic.validation_stage = "empty_choices"
                    raise
                trace = CURRENT_TRACE.get()
                assert trace is not None
                trace.reply(reply)
                return reply

        return cast(DecisionClient, Observed())

    async def task_diff(self, task: str, diff: str, target: ChangeTarget) -> None:
        outcome, diagnostic = await self.decide(
            messages(task, diff=diff, target=target),
            target,
            "task-and-diff",
            asyncio.get_running_loop().time() + self.arm.limits.call_s,
        )
        self.evidence.append(
            DecisionEvidence(
                target=target,
                node_id="task-and-diff",
                task=task,
                difference=None,
                diagnostics=diagnostic,
                choice=outcome.choice,
                unavailable=outcome.unavailable,
            )
        )

    async def spark(
        self, prompt: list[dict], target: ChangeTarget, node: str, deadline: float
    ) -> DecisionOutcome:
        """Same inherited strict schema, explicit low reasoning and product retry accounting.

        classify_difference itself refuses Meta. Keep its exact prompt and constrained schema,
        and validate Meta's actual response identity without relabeling it as Spark Open.
        """
        assert self.client is not None
        scope = CallScope(deadline, self.budget.accounting, "review", self.budget.admission, True)
        token = CALL_SCOPE.set(scope)
        try:
            async with asyncio.timeout_at(deadline):
                reply = await self.observed_client().complete(prompt, [])
            if (
                reply.model != MUSE_SPARK.model
                or reply.finish_reason != "stop"
                or reply.tool_calls
                or not reply.text
                or len(reply.text) > 512
            ):
                raise ValueError("invalid envelope")
            data = json.loads(reply.text, object_pairs_hook=unique)
            if (
                not isinstance(data, dict)
                or set(data) != {"choice"}
                or data["choice"] not in CHOICES
            ):
                raise ValueError("invalid choice")
            return DecisionSelected(target, node, data["choice"])
        except DecisionUnavailable as error:
            reason = error.code
        except TimeoutError:
            reason = "deadline"
        except EmptyChoicesError:
            reason = "invalid_response"
        except ModelAPIError:
            reason = "transport"
        except (ValueError, TypeError, KeyError, AttributeError, BenchError):
            reason = "invalid_response"
        finally:
            CALL_SCOPE.reset(token)
        return DecisionMissing(target, node, reason)
