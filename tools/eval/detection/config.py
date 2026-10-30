"""Committed arm definitions and provider-free projections; never load settings or secrets."""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from tools.eval.detection.product_limits import completion_limit
from tools.eval.grade import Tariff
from tools.eval.tasks import BenchError, digest

from oversight.agents import D6_RESPONSE_FORMAT, D6_SYSTEM_PROMPT, MUSE_SPARK, MUSE_SPARK_OPEN
from oversight.changes import ReviewCardLimits

VERSION = "detection-d6-v1"
NO_BEHAVIOR = "No behavior evidence was collected. This is not a measurement of equivalence."
PROTOCOL = (
    "Arms 2 and 3 use the D6 decision prompt and schema on Muse Spark, not product cross-review."
)


def system_prompt() -> str:
    """Use the public product prompt without constructing clients or loading settings."""
    return D6_SYSTEM_PROMPT


@dataclass(frozen=True)
class Arm:
    id: str
    profile: str
    role: Literal["tests-only", "task/diff", "behavior-Spark", "behavior-Spark-Open"]
    max_output_tokens: int | None = None
    limits: ReviewCardLimits = field(default_factory=ReviewCardLimits)

    @property
    def behavior(self) -> bool:
        return self.role in {"behavior-Spark", "behavior-Spark-Open"}

    def configuration(self, fake: bool, rates: dict[str, Tariff]) -> dict:
        model = {"meta": MUSE_SPARK, "baseten": MUSE_SPARK_OPEN}.get(self.profile)
        prompt = system_prompt() if model else "tests only"
        template = {
            "system": prompt,
            "arm_id": self.id,
            "response_format": D6_RESPONSE_FORMAT if model else None,
            "adapter_source_sha256": digest(Path(__file__).with_name("adapters.py").read_text())
            if model
            else None,
        }
        config = {
            "version": "detection-d6-v2" if model else VERSION,
            "arm_id": self.id,
            "fake": fake,
            "profile": self.profile,
            "model": model.model if model else "none",
            "reasoning": "low" if model else None,
            "behavior": self.behavior,
            "prompt_version": "detection-d6-v2" if model else VERSION,
            "prompt_sha256": fingerprint(template),
            "system_prompt_sha256": digest(prompt),
            "response_format_sha256": fingerprint(D6_RESPONSE_FORMAT) if model else None,
            "input_fields": ["target", "task", "diff", "behavior_evidence"]
            if self.role == "task/diff"
            else (["target", "task", "difference", "confirmed_examples"] if model else []),
            "no_behavior": NO_BEHAVIOR if self.role == "task/diff" else None,
            "max_output_tokens": self.max_output_tokens if model else 512,
            "retry_attempts": 5,
            "call_s": self.limits.call_s if model else 30,
            "total_s": self.limits.total_s if model else 120,
            "classifications": self.limits.classifications if model else 8,
            "test_timeout_s": 30,
            "measured_cone": False,
            "cross_review": False,
            "tariff": rates[self.profile].model_dump() if model else None,
            "protocol": PROTOCOL,
        }

        if model:
            if self.max_output_tokens is None or self.max_output_tokens <= 0:
                raise BenchError("model arm needs a positive completion allowance")
            config.update(
                role=self.role,
                deadline_source="oversight.changes.ReviewCardLimits defaults",
                completion_limit_source="oversight.decision_config.configured_decision",
                diagnostics_source_sha256=digest(
                    Path(__file__).with_name("diagnostics.py").read_text()
                ),
            )
        return config


ARMS = (
    Arm("detect-1", "none", "tests-only"),
    Arm("detect-2-v2", "meta", "task/diff", completion_limit()),
    Arm("detect-3-v2", "meta", "behavior-Spark", completion_limit()),
    Arm("detect-4-v3", "baseten", "behavior-Spark-Open", completion_limit()),
)


SUPERSEDED_ARMS = {"detect-2", "detect-3", "detect-4", "detect-4-v2"}
PILOT_PATCHES = {
    "tests-only": "d01",
    "task/diff": "d02",
    "behavior-Spark": "d01",
    "behavior-Spark-Open": "d01",
}


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
    pilot_costs: dict[str, float] | None = None,
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
    worst = {
        arm.id: worst_case_per_job(
            arm.configuration(False, rates)
            | {
                "assumptions": assumptions.model_dump(),
            }
        )
        for arm in ARMS
    }
    pilots = {arm.id: (pilot_costs or {}).get(arm.id) for arm in ARMS}
    admitted = {
        arm.id: max((pilot_costs or {}).get(arm.id, costs[arm.id]), worst[arm.id]) for arm in ARMS
    }
    total = spent + sum(admitted.values()) * (1 + 20 * repetitions)
    if total > cap:
        raise BenchError(f"projection ${total:.6f} exceeds cap; reduce repetitions before arms")
    return {
        "method": "declared token/call estimates, not a billing ceiling",
        "assumptions": assumptions.model_dump(),
        "spent_usd": spent,
        "estimated_total_usd": total,
        "per_attempt_usd": admitted,
        "declared_per_job_usd": costs,
        "pilot_per_job_usd": pilots,
        "worst_case_per_job_usd": worst,
        "attempts": 4 + 20 * (1 + 3 * repetitions),
        "cap_usd": cap,
    }


def fingerprint(configuration: dict) -> str:
    return digest(json.dumps(configuration, sort_keys=True, separators=(",", ":")))


def worst_case_per_job(config: dict) -> float:
    if config["tariff"] is None:
        return 0.0
    assumptions = config["assumptions"]
    tariff = config["tariff"]
    return (
        assumptions["calls"]
        * (
            assumptions["input_tokens"] * tariff["input"]
            + config["max_output_tokens"] * tariff["output"]
        )
        / 1e6
    )
