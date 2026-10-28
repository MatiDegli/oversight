"""One locked, durable ledger per output directory; crashes never free a reservation."""

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tools.eval.grade import Cost, Result, Tariff
from tools.eval.identity import Identity
from tools.eval.tasks import BenchError, Task


class Record(Identity):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(frozen=True)
    task_hash: str = Field(frozen=True)
    profile: str = Field(frozen=True)
    run_id: str
    allowance_usd: float = Field(gt=0, allow_inf_nan=False)
    charged_usd: float = Field(default=0, ge=0, allow_inf_nan=False)
    unknown: bool = False
    agent_id: str | None = None
    change_id: str | None = None
    status: Literal["reserved", "finished"] = "reserved"
    result: Result | None = None
    tariff: Tariff | None = None
    pilot: bool = False


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[2] = 2
    cap_usd: float = Field(gt=0, allow_inf_nan=False)
    results_file: str
    tariff: Tariff | None
    records: list[Record] = Field(default_factory=list)
    plans: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def upgrade(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("version") == 1:
            # Keep all charges, unknown flags and reservations, including unclean finishes.
            value = dict(value, version=2)
            records = []
            for index, old in enumerate(value.get("records", [])):
                record = dict(old)
                identity = dict(
                    arm_id=record["profile"],
                    repetition=1,
                    config_hash="legacy",
                    attempt_id=f"legacy-{index}-{record['run_id']}",
                )
                record.update(identity)
                record["tariff"] = value.get("tariff")
                if record.get("result"):
                    record["result"] = dict(record["result"], profile=record["profile"], **identity)
                records.append(record)
            value["records"] = records
        return value


def atomic_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=".bench-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Ledger:
    def __init__(
        self, out: Path, cap: float, tariff: Tariff | None, *, multi_tariff: bool = False
    ) -> None:
        if out.suffix != ".jsonl" or out.name.startswith("events-"):
            raise BenchError(
                "--out must be a results .jsonl filename, outside reserved evidence names"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        self.out = out
        self.path = out.parent / "ledger.json"
        self.lock = (out.parent / "ledger.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                self.state = State.model_validate_json(self.path.read_text())
                if (self.state.cap_usd, self.state.results_file) != (cap, out.name) or (
                    not multi_tariff and self.state.tariff != tariff
                ):
                    raise BenchError("ledger cap, output filename or tariff changed")
            else:
                if out.exists():
                    raise BenchError("results exist without their ledger")
                self.state = State(cap_usd=cap, results_file=out.name, tariff=tariff)
                self.save()
            # Regenerate from durable records after a crash between the two atomic writes.
            self.save()
            self.results()
        except BaseException:
            self.lock.close()
            raise

    def close(self) -> None:
        self.lock.close()

    def save(self) -> None:
        atomic_write(self.path, self.state.model_dump_json(indent=2) + "\n")

    def results(self) -> None:
        atomic_write(
            self.out,
            "".join(r.result.model_dump_json() + "\n" for r in self.state.records if r.result),
        )

    def ready(self) -> None:
        if any(r.unknown for r in self.state.records):
            raise BenchError("ledger has unknown cost; reconcile before another bench")
        if any(r.status != "finished" for r in self.state.records):
            raise BenchError("unsettled reservation: never retry an interrupted paid task")
        if self.spent >= self.state.cap_usd:
            raise BenchError("cap exhausted")

    @property
    def spent(self) -> float:
        return sum(r.charged_usd for r in self.state.records)

    def completed(
        self,
        task: Task,
        profile: str,
        arm_id: str = "legacy",
        repetition: int = 1,
        config_hash: str = "legacy",
    ) -> bool:
        records = [
            r
            for r in self.state.records
            if r.task_id == task.id and r.profile == profile and r.arm_id == arm_id
        ]
        if any(r.task_hash != task.hash for r in records):
            raise BenchError("task changed after its recorded attempt")
        self.bind(arm_id, config_hash)
        return any(r.repetition == repetition and r.status == "finished" for r in records)

    def bind(self, arm_id: str, config_hash: str) -> None:
        if any(
            r.arm_id == arm_id and r.config_hash not in {config_hash, "legacy"}
            for r in self.state.records
        ):
            raise BenchError("arm configuration changed; choose a new arm_id")

    def check_allowance(
        self,
        task: Task,
        profile: str,
        allowance: float,
        **identity: Any,
    ) -> None:
        self.ready()
        if self.completed(task, profile, **identity):
            raise BenchError("whole-task retry refused")
        if self.spent + allowance > self.state.cap_usd:
            raise BenchError("cap cannot reserve the next task allowance")

    def reserve(
        self,
        task: Task,
        profile: str,
        run_id: str,
        allowance: float,
        *,
        arm_id: str = "legacy",
        repetition: int = 1,
        config_hash: str = "legacy",
        tariff: Tariff | None = None,
        pilot: bool = False,
    ) -> Record:
        identity: dict[str, Any] = dict(
            arm_id=arm_id, repetition=repetition, config_hash=config_hash
        )
        self.check_allowance(task, profile, allowance, **identity)
        record = Record(
            task_id=task.id,
            task_hash=task.hash,
            profile=profile,
            run_id=run_id,
            allowance_usd=allowance,
            attempt_id=uuid4().hex,
            tariff=tariff,
            pilot=pilot,
            **identity,
        )
        self.state.records.append(record)
        self.save()  # persisted before POST /agents
        return record

    def charge(self, record: Record, cost: Cost) -> bool:
        if cost == "unknown":
            record.unknown = True
        else:
            record.charged_usd += cost
        self.save()  # before another event or task is processed
        return (
            record.unknown
            or self.spent >= self.state.cap_usd
            or record.charged_usd >= record.allowance_usd
        )

    def finish(self, record: Record, result: Result, *, cleaned: bool) -> None:
        if any(
            getattr(result, key) != getattr(record, key)
            for key in (*Identity.model_fields, "profile", "task_id")
        ):
            raise BenchError("result identity differs from its reservation")
        record.result = result
        if result.cost_usd == "unknown":
            record.unknown = True
        record.status = "finished" if cleaned else "reserved"
        self.save()
        self.results()


def manifest(path: Path, run_id: str, values: dict) -> None:
    """Keep every server run's exact advertisement, assumptions and task fingerprints."""
    existing = json.loads(path.read_text()) if path.exists() else {"runs": {}}
    existing["runs"][run_id] = values
    atomic_write(path, json.dumps(existing, indent=2, allow_nan=False) + "\n")
