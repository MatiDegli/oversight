"""Named configurations and a single paid-bench accounting location."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tools.eval.grade import Tariff
from tools.eval.session import PROFILES
from tools.eval.tasks import REPO, BenchError, digest

from oversight.agents import PROMPT_VERSION
from oversight.app import LIMITS
from oversight_contracts.events import RunStarted

PAID_OUTPUT = REPO / ".eval-output" / "bench" / "results.jsonl"


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
            "prompt_version": run.prompt_version if run else PROMPT_VERSION,
            "harness_pack": self.harness_pack,
        }

    def config_hash(self, run: RunStarted | None = None) -> str:
        return digest(json.dumps(self.configuration(run), sort_keys=True, separators=(",", ":")))


def load_arms(path: Path, selected: list[str]) -> list[Arm]:
    arms = [Arm.model_validate(item) for item in json.loads(path.read_text())]
    if not arms or len({a.arm_id for a in arms}) != len(arms):
        raise BenchError("arms must have unique names")
    if set(selected) - {a.arm_id for a in arms}:
        raise BenchError("unknown arm_id")
    return [a for a in arms if not selected or a.arm_id in selected]


def output_path(out: Path, *, paid: bool) -> Path:
    out = out.resolve()
    if paid:
        if out != PAID_OUTPUT.resolve():
            raise BenchError(f"paid --out must resolve to {PAID_OUTPUT}")
        # Older directories cannot silently disappear from cumulative accounting.
        older = [
            p
            for p in PAID_OUTPUT.parent.parent.rglob("ledger.json")
            if p.parent not in {PAID_OUTPUT.parent, PAID_OUTPUT.parent.parent / "lead"}
        ]
        if older:
            raise BenchError("legacy bench ledgers need consolidation before paid runs")
    return out
