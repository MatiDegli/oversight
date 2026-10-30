"""Pilot then score identical patches, preserving every canonical ledger reservation."""

import asyncio
import random
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from tools.eval.behavior.catalog import check_baseline, load_patches
from tools.eval.detection.accounting import (
    Budget,
    completed,
    pilot_projection,
    require_model_call,
    task_for,
)
from tools.eval.detection.adapters import Adapter
from tools.eval.detection.config import (
    ARMS,
    PILOT_PATCHES,
    Arm,
    Assumptions,
    fingerprint,
    paid_gate,
    worst_case_per_job,
)
from tools.eval.detection.runtime import measure
from tools.eval.detection.transport import TransportClient
from tools.eval.grade import Result, Tariff, Tests, Tokens
from tools.eval.ledger import Ledger
from tools.eval.paths import PAID_OUTPUT as CANONICAL_OUTPUT
from tools.eval.paths import output_path
from tools.eval.tasks import BenchError


def jobs_for(
    rates: dict[str, Tariff],
    fake_choice: str | None,
    pilot: bool,
    repetitions: int,
    labels_hash: str | None,
) -> list[tuple]:
    if repetitions < 1 or (pilot and repetitions != 1):
        raise BenchError("pilots require one repetition; bench repetitions must be positive")
    if not pilot and labels_hash is None:
        raise BenchError("scored execution requires verified external labels")
    patches = load_patches()
    selected = [p for p in patches if p.set == ("dev" if pilot else "bench")]
    jobs = []
    for arm in ARMS:
        config = arm.configuration(fake_choice is not None, rates) | {"fake_choice": fake_choice}
        for patch in (
            [p for p in selected if p.id == PILOT_PATCHES[arm.role]] if pilot else selected
        ):
            for rep in range(1, 1 + (1 if arm.profile == "none" else repetitions)):
                jobs.append((patch, arm, config, rep))
    return jobs


def save_plan(
    ledger: Ledger,
    jobs: list[tuple],
    seed: int,
    assumptions: Assumptions,
    labels_hash: str | None,
    pilot: bool,
) -> str:
    specification = {
        "kind": "detection",
        "pilot": pilot,
        "labels_sha256": labels_hash,
        "assumptions": assumptions.model_dump(),
        "configurations": {arm.id: config for _, arm, config, _ in jobs},
        "jobs": [
            {
                "patch": p.id,
                "patch_sha256": p.sha256,
                "arm": a.id,
                "config_hash": fingerprint(c),
                "repetition": r,
            }
            for p, a, c, r in jobs
        ],
    }
    plan_id = fingerprint(specification)
    previous = ledger.state.plans.get(plan_id)
    if previous is not None and previous["seed"] != seed:
        raise BenchError("resume must keep the recorded seed")
    # Labels pins and assumptions are immutable across score plans, even with fewer repetitions.
    for plan in ledger.state.plans.values():
        if (
            plan.get("kind") == "detection"
            and not plan["pilot"]
            and not pilot
            and (
                plan["labels_sha256"] != labels_hash
                or plan["assumptions"] != assumptions.model_dump()
                or plan["seed"] != seed
            )
        ):
            raise BenchError("labels or declared assumptions changed on resume")
    random.Random(seed).shuffle(jobs)
    ledger.state.plans[plan_id] = {
        **specification,
        "seed": seed,
        "order": [(p.id, a.id, r) for p, a, _, r in jobs],
    }
    ledger.save()
    return plan_id


async def run_attempt(
    ledger: Ledger,
    job: tuple,
    assumptions: Assumptions,
    demo_python: Path,
    *,
    fake_choice: str | None,
    pilot: bool,
    labels_hash: str | None,
    factory: Callable[[Arm], TransportClient] | None,
) -> None:
    patch, arm, config, rep = job
    fake = fake_choice is not None
    tariff = (
        Tariff.model_validate(config["tariff"]) if config["tariff"] else Tariff(input=0, output=0)
    )
    per_send = max(
        1e-9,
        worst_case_per_job(config) / assumptions.calls,
    )
    record = ledger.reserve(
        task_for(patch),
        arm.profile,
        uuid4().hex,
        per_send * assumptions.calls,
        arm_id=arm.id,
        repetition=rep,
        config_hash=fingerprint(config),
        tariff=tariff,
        pilot=pilot,
    )
    budget = Budget(ledger, record, tariff, per_send, fake=fake)
    client = None
    started = asyncio.get_running_loop().time()
    try:
        if not fake and arm.profile != "none":
            if factory is None:
                raise BenchError(f"{arm.id}: unavailable provider adapter")
            client = factory(arm)
            if not isinstance(client, TransportClient):
                raise BenchError("detection requires a transport-counted client")
            client.budget = budget
        adapter = Adapter(
            arm, budget, client=client, fake_choice=fake_choice if arm.profile != "none" else None
        )
        measured = await measure(
            patch,
            arm,
            config,
            adapter,
            ledger.out.parent / "detection" / record.attempt_id,
            demo_python,
            labels_hash,
            record.run_id,
        )
    finally:
        if client is not None:
            await client.aclose()
        if budget.sends != budget.receipts:
            ledger.charge(record, "unknown")
    no_calls = budget.sends == 0
    result = Result(
        **{k: getattr(record, k) for k in ("arm_id", "repetition", "config_hash", "attempt_id")},
        profile=arm.profile,
        task_id=record.task_id,
        agent_model="explicit-fake" if fake else config["model"],
        outcome="stopped",
        steps=budget.sends,
        turns=budget.sends,
        duration_s=asyncio.get_running_loop().time() - started,
        tokens=Tokens(input=0, output=0) if no_calls else budget.usage.tokens,
        cost_usd=0 if no_calls else budget.usage.cost,
        test_result=Tests(
            baseline="passed" if measured.tests_passed else "failed",
            hidden="not_run",
            policy="not_run",
        ),
        detection=measured,
    )
    ledger.finish(record, result, cleaned=True)
    ledger.ready()
    if pilot:
        require_model_call(arm, measured, result.steps)
    if any("budget_blocked" in reason for reason in measured.unavailable):
        raise BenchError("attempt allowance exhausted; stopping")


async def run_plan(
    ledger: Ledger,
    rates: dict[str, Tariff],
    assumptions: Assumptions,
    demo_python: Path,
    *,
    pilot: bool,
    labels_hash: str | None = None,
    fake_choice: str | None = None,
    paid: bool = False,
    repetitions: int = 1,
    seed: int = 42,
    factory: Callable[[Arm], TransportClient] | None = None,
) -> dict:
    if fake_choice is None:
        paid_gate(
            paid,
            ledger.state.cap_usd,
            (rates["meta"].input, rates["meta"].output),
            (rates["baseten"].input, rates["baseten"].output),
        )
        output_path(ledger.out, paid=True)
    elif paid or ledger.out.resolve().parent == CANONICAL_OUTPUT.resolve().parent:
        raise BenchError("explicit fake runs must use a separate nonpaid output")
    ledger.ready()
    check_baseline()
    jobs = jobs_for(rates, fake_choice, pilot, repetitions, labels_hash)
    for _, _, config, _ in jobs:
        config["assumptions"] = assumptions.model_dump()
    if not pilot:
        pilot_projection(ledger, jobs)
    else:
        for patch, arm, config, rep in jobs:
            ledger.bind(arm.id, fingerprint(config))
            old = [r for r in ledger.state.records if r.arm_id == arm.id and r.pilot]
            if old and not completed(ledger, patch, arm, config, rep):
                raise BenchError("pilot identity changed; never retry a pilot")
        allowances = 0.0
        for _, _arm, config, _ in (j for j in jobs if not completed(ledger, *j)):
            allowances += max(1e-9 * assumptions.calls, worst_case_per_job(config))
        if ledger.spent + allowances > ledger.state.cap_usd:
            raise BenchError("projection cannot reserve all four pilot allowances")
    plan_id = save_plan(ledger, jobs, seed, assumptions, labels_hash, pilot)
    for job in jobs:
        if completed(ledger, *job):
            continue
        if not pilot:
            pilot_projection(ledger, jobs)
        await run_attempt(
            ledger,
            job,
            assumptions,
            demo_python,
            fake_choice=fake_choice,
            pilot=pilot,
            labels_hash=labels_hash,
            factory=factory,
        )
    return {
        "plan_id": plan_id,
        "seed": seed,
        "spend_usd": ledger.spent,
        "projection": None if pilot else pilot_projection(ledger, jobs),
    }
