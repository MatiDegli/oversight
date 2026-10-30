"""Bounded metadata only; observe replies before the product's strict validation."""

import json
from contextvars import ContextVar
from dataclasses import dataclass, field

import openai
from openai.types.chat import ChatCompletion
from tools.eval.detection.accounting import Budget
from tools.eval.detection_types import CallDiagnostics, ValidationStage

from oversight.agents import DecisionClient, DecisionUnavailable, ModelReply
from oversight.agents.client import EmptyChoicesError, reply_of, usage_of
from oversight.agents.decision import CHOICES


@dataclass
class Trace:
    expected_model: str
    diagnostic: CallDiagnostics = field(default_factory=CallDiagnostics)

    def reply(self, reply: ModelReply) -> None:
        d = self.diagnostic
        d.reported_model = reply.model[:256] if reply.model is not None else None
        d.finish_reason = reply.finish_reason[:64] if reply.finish_reason is not None else None
        d.reply_characters = len(reply.text or "")
        for name, value in (
            ("completion_tokens", reply.usage.output_tokens),
            ("reasoning_tokens", reply.usage.reasoning_tokens),
        ):
            setattr(d, name, value if type(value) is int and value >= 0 else None)
        d.validation_stage = validation_stage(reply, self.expected_model)

    def raw(self, completion: ChatCompletion) -> None:
        if not completion.choices:
            try:
                reply_of(completion)
            except EmptyChoicesError as error:
                self.reply(error.reply)
            self.diagnostic.validation_stage = "empty_choices"
            return
        message = completion.choices[0].message
        if message is None:
            self.reply(
                ModelReply(
                    None,
                    [],
                    usage_of(completion),
                    completion.model,
                    completion.choices[0].finish_reason,
                )
            )
            self.diagnostic.validation_stage = "message"
            return
        self.reply(reply_of(completion))
        if len(completion.choices) != 1:
            self.diagnostic.validation_stage = "choice_count"
        elif message.refusal:
            self.diagnostic.validation_stage = "refusal"
        elif message.function_call is not None:
            self.diagnostic.validation_stage = "legacy_tool_call"


CURRENT_TRACE: ContextVar[Trace | None] = ContextVar("detection_trace", default=None)


class DetectionClient(DecisionClient):
    """Retain only bounded metadata before SDK envelope checks; no response persistence."""

    def __init__(self, *args, budget: Budget | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.budget = budget

    async def _completion(self, params: dict[str, object]) -> ChatCompletion:
        if self.budget is None:
            raise DecisionUnavailable("budget_unconfigured")
        self.budget.sent()  # Durable before the first await can enter the transport.
        try:
            completion = await super()._completion(params)
        except openai.APITimeoutError as error:
            # The receipt remains unknown. Do not mask this timeout with a later retry denial.
            raise TimeoutError from error
        trace = CURRENT_TRACE.get()
        if trace is not None:
            trace.raw(completion)
        return completion


def validation_stage(reply: ModelReply, expected_model: str) -> ValidationStage:
    if reply.model != expected_model:
        return "model"
    if reply.finish_reason != "stop":
        return "finish_reason"
    if reply.tool_calls:
        return "tool_calls"
    if not reply.text:
        return "empty_text"
    if len(reply.text) > 512:
        return "reply_length"
    duplicate = False

    def unique(pairs):
        nonlocal duplicate
        result = {}
        for key, value in pairs:
            if key in result:
                duplicate = True
            result[key] = value
        return result

    try:
        data = json.loads(reply.text, object_pairs_hook=unique)
    except (ValueError, TypeError):
        return "json"
    if duplicate:
        return "duplicate_key"
    if not isinstance(data, dict):
        return "object"
    if set(data) != {"choice"}:
        return "fields"
    if data["choice"] not in CHOICES:
        return "choice"
    return "none"
