"""Named configurations and a single paid-bench accounting location."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tools.eval.grade import Tariff
from tools.eval.paths import PAID_OUTPUT as PAID_OUTPUT
from tools.eval.paths import output_path as canonical_path
from tools.eval.session import PROFILES
from tools.eval.tasks import BenchError, digest

from oversight.agents import prompt_version
from oversight.app import LIMITS
from oversight_contracts.events import RunStarted


class Arm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    arm_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    profile: Literal["fake", "muse-spark", "muse-spark-open"]
    reasoning: Literal["none", "low", "medium", "high"] | None
    harness_pack: Literal["on", "off"]
    tariff: Tariff | None = None

    @model_validator(mode="after")
    def validate_reasoning(self) -> "Arm":
        if self.profile != "fake" and self.reasoning is None:
            raise ValueError("paid arms require an explicit reasoning setting")
        if self.profile == "fake" and self.reasoning is not None:
            raise ValueError("the scripted fake has no reasoning setting")
        if (
            self.profile != "fake"
            and self.tariff is not None
            and (self.tariff.input <= 0 or self.tariff.output <= 0)
        ):
            raise ValueError("paid arms require positive tariffs")
        return self

    def configuration(self, run: RunStarted | None = None) -> dict:
        return {
            "profile": self.profile,
            "model": PROFILES[self.profile][1],
            "reasoning": self.reasoning,
            "limits": (run.limits if run else LIMITS).model_dump(mode="json"),
            "prompt_version": run.prompt_version
            if run
            else prompt_version(self.harness_pack == "on"),
            "harness_pack": self.harness_pack,
        }

    def config_hash(self, run: RunStarted | None = None) -> str:
        return digest(json.dumps(self.configuration(run), sort_keys=True, separators=(",", ":")))


def validate_pair(arms: list[Arm]) -> None:
    for left in arms:
        for right in arms:
            if left.harness_pack == right.harness_pack:
                continue
            fields = [
                f
                for f in ("profile", "reasoning", "tariff")
                if getattr(left, f) != getattr(right, f)
            ]
            if fields:
                raise BenchError(
                    f"pair {left.arm_id}/{right.arm_id} differs in: {', '.join(fields)}"
                )


def load_arms(path: Path, selected: list[str]) -> list[Arm]:
    arms = [Arm.model_validate(item) for item in json.loads(path.read_text())]
    if not arms or len({a.arm_id for a in arms}) != len(arms):
        raise BenchError("arms must have unique names")
    if set(selected) - {a.arm_id for a in arms}:
        raise BenchError("unknown arm_id")
    chosen = [a for a in arms if not selected or a.arm_id in selected]
    validate_pair(chosen)
    return chosen


def output_path(out: Path, *, paid: bool) -> Path:
    return canonical_path(out, paid=paid, canonical=PAID_OUTPUT)
