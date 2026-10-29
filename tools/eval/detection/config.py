"""Committed arm definitions and provider-free projections; never load settings or secrets."""

import ast
import inspect
import json
import math
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from tools.eval.grade import Tariff
from tools.eval.tasks import BenchError, digest

from oversight.agents import MUSE_SPARK, MUSE_SPARK_OPEN, DecisionClient, classify_difference

VERSION = "detection-d6-v1"
NO_BEHAVIOR = "No behavior evidence was collected. This is not a measurement of equivalence."
PROTOCOL = (
    "Arms 2 and 3 use the D6 decision prompt and schema on Muse Spark, not product cross-review."
)


def system_prompt() -> str:
    """Import the literal from the actual public function; fail closed on backend restructuring.

    The backend exposes no prompt constant. Extract its system content from its AST rather than
    maintaining a second copy. No provider object, settings, code execution, or network is needed.
    A parity test also captures the real function's outgoing request.
    """
    tree = ast.parse(inspect.getsource(classify_difference))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        fields = {
            k.value: v
            for k, v in zip(node.keys, node.values, strict=False)
            if isinstance(k, ast.Constant)
        }
        role, content = fields.get("role"), fields.get("content")
        if (
            isinstance(role, ast.Constant)
            and role.value == "system"
            and isinstance(content, ast.Constant)
            and isinstance(content.value, str)
        ):
            found.append(content.value)
    if len(found) != 1:
        raise BenchError("D6 system prompt unavailable; backend public export required")
    return found[0]


@dataclass(frozen=True)
class Arm:
    id: str
    profile: str
    behavior: bool

    def configuration(self, fake: bool, rates: dict[str, Tariff]) -> dict:
        model = {"meta": MUSE_SPARK, "baseten": MUSE_SPARK_OPEN}.get(self.profile)
        prompt = system_prompt() if model else "tests only"
        template = {
            "system": prompt,
            "arm_id": self.id,
            "d6_source": inspect.getsource(classify_difference) if model else None,
            "adapter_source_sha256": digest(Path(__file__).with_name("adapters.py").read_text())
            if model
            else None,
        }
        return {
            "version": VERSION,
            "arm_id": self.id,
            "fake": fake,
            "profile": self.profile,
            "model": model.model if model else "none",
            "reasoning": "low" if model else None,
            "behavior": self.behavior,
            "prompt_version": VERSION,
            "prompt_sha256": fingerprint(template),
            "system_prompt_sha256": digest(prompt),
            "schema_source_sha256": digest(inspect.getsource(DecisionClient.params))
            if model
            else None,
            "input_fields": ["target", "task", "diff", "behavior_evidence"]
            if self.id == "detect-2"
            else (["target", "task", "difference", "confirmed_examples"] if model else []),
            "no_behavior": NO_BEHAVIOR if self.id == "detect-2" else None,
            "max_output_tokens": 512,
            "retry_attempts": 5,
            "call_s": 30,
            "total_s": 120,
            "classifications": 8,
            "test_timeout_s": 30,
            "measured_cone": False,
            "cross_review": False,
            "tariff": rates[self.profile].model_dump() if model else None,
            "protocol": PROTOCOL,
        }


ARMS = (
    Arm("detect-1", "none", False),
    Arm("detect-2", "meta", False),
    Arm("detect-3", "meta", True),
    Arm("detect-4", "baseten", True),
)


class Assumptions(BaseModel):
    input_tokens: int = Field(gt=0, strict=True)
    output_tokens: int = Field(gt=0, strict=True)
    calls: int = Field(gt=0, strict=True)


def rates_for(meta: tuple | None, baseten: tuple | None) -> dict[str, Tariff]:
    rates = {}
    for name, pair in (("meta", meta), ("baseten", baseten)):
        if pair is None or any(not math.isfinite(n) or n <= 0 for n in pair):
            raise BenchError("finite positive Meta and Baseten input/output tariffs required")
        rates[name] = Tariff(input=pair[0], output=pair[1])
    return rates


def paid_gate(
    paid: bool, cap: float, meta: tuple | None, baseten: tuple | None
) -> dict[str, Tariff]:
    if not paid:
        raise BenchError("paid execution requires explicit --paid")
    if not math.isfinite(cap) or not 0 < cap <= 20:
        raise BenchError("shared cap must be finite, positive, and at most US$20")
    return rates_for(meta, baseten)


def projection(
    rates: dict[str, Tariff],
    assumptions: Assumptions,
    repetitions: int,
    *,
    spent: float,
    cap: float,
) -> dict:
    if repetitions < 1 or not math.isfinite(cap) or not 0 < cap <= 20:
        raise BenchError("invalid repetitions or shared cap")
    costs = {
        a.id: (
            assumptions.input_tokens * rates[a.profile].input
            + assumptions.output_tokens * rates[a.profile].output
        )
        * assumptions.calls
        / 1e6
        if a.profile != "none"
        else 0
        for a in ARMS
    }
    total = spent + sum(costs.values()) * (1 + 20 * repetitions)
    if total > cap:
        raise BenchError(f"projection ${total:.6f} exceeds cap; reduce repetitions before arms")
    return {
        "method": "declared token/call estimates, not a billing ceiling",
        "assumptions": assumptions.model_dump(),
        "spent_usd": spent,
        "estimated_total_usd": total,
        "per_attempt_usd": costs,
        "attempts": 4 + 20 * (1 + 3 * repetitions),
        "cap_usd": cap,
    }


def fingerprint(configuration: dict) -> str:
    return digest(json.dumps(configuration, sort_keys=True, separators=(",", ":")))
