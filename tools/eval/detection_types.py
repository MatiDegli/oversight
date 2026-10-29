"""Typed durable detection evidence, independent of legacy task-solving outcomes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from oversight_contracts.review import ChangeFacts, ChangeTarget, NodeBehaviorDiff

type ValidationStage = Literal[
    "model",
    "finish_reason",
    "tool_calls",
    "empty_text",
    "reply_length",
    "json",
    "duplicate_key",
    "object",
    "fields",
    "choice",
    "empty_choices",
    "choice_count",
    "message",
    "refusal",
    "legacy_tool_call",
    "transport",
    "deadline",
    "profile",
    "credentials",
    "closed",
    "budget_unconfigured",
    "pricing_unconfigured",
    "budget_blocked",
    "ledger_unknown",
    "ledger_unreliable",
    "none",
]


class CallDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reported_model: str | None = Field(default=None, max_length=256)
    finish_reason: str | None = Field(default=None, max_length=64)
    reply_characters: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    latency_s: float = Field(default=0, ge=0, allow_inf_nan=False)
    validation_stage: ValidationStage = "none"


class DecisionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: ChangeTarget
    node_id: str
    task: str
    difference: NodeBehaviorDiff | None
    choice: Literal["asked_for", "not_asked_for", "cannot_tell"] | None = None
    unavailable: str | None = None
    diagnostics: CallDiagnostics | None = None


class DetectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    patch_id: str = Field(pattern=r"^[bd]\d{2}$")
    patch_sha256: str
    labels_sha256: str | None
    configuration: dict
    prompt_version: str
    prompt_sha256: str
    protocol: str = "Arms 2 and 3 use D6 on Muse Spark, not product cross-review."
    facts: list[ChangeFacts] = Field(default_factory=list)
    decisions: list[DecisionEvidence] = Field(default_factory=list)
    tests_passed: bool
    detected: bool | None
    unprobed: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    let_through: bool
    question: bool
    calibration: Literal["not available"] = "not available"
