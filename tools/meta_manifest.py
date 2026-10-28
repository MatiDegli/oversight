"""Describe model execution from a recorded JSONL log; no model calls or credits."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field, TypeAdapter

from oversight_contracts.events import ModelProfile, RunStarted, ServerEvent, UsageTick
from recording import Provenance, RecordError, provenance_path

EVENT = TypeAdapter(ServerEvent)
FAKE = "scripted fake model"
METRICS = ("input_tokens", "output_tokens", "cached_tokens", "cost_usd")


class AgentManifest(BaseModel):
    agent_id: str
    slot: str | None
    requested_profile: str | None
    requested_model: str | None
    provider: str | None
    reported_models: dict[str, int]
    unknown_model_calls: int
    mode: str | None
    prompt_version: str
    limits: dict[str, int]
    final_status: str | None
    decision: str | None
    merge_result: str | None
    errors: int
    fatal_errors: int
    first_event_time: str
    last_event_time: str
    calls: int
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cached_input_share: float | None
    cost_usd: float | None
    unknown_usage_calls: int
    unknown_calls: dict[str, int]


class Manifest(BaseModel):
    run_id: str
    recording_kind: str
    provenance: Provenance
    first_event_time: str
    last_event_time: str
    merge_mode: str | None
    agents: list[AgentManifest]
    checks: list[str] = Field(default_factory=list)


def read_log(log: Path) -> tuple[list[ServerEvent], Provenance]:
    """Bind validated contract events to the exporter's receipt, without rescanning secrets."""
    raw = log.read_bytes()
    events: list[ServerEvent] = []
    for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = EVENT.validate_json(line)
        except ValueError:
            raise RecordError(f"invalid contract event at line {number}") from None
        if event.seq != len(events) + 1:
            raise RecordError(f"event sequence gap at line {number}")
        if event.type == "run.started" and events:
            raise RecordError("the log contains another run.started")
        events.append(event)
    if not events or events[0].type != "run.started":
        raise RecordError("the log must start with run.started")
    try:
        provenance = Provenance.model_validate_json(provenance_path(log).read_bytes())
    except (OSError, ValueError):
        raise RecordError("exporter provenance is missing or invalid") from None
    if (
        provenance.sha256 != hashlib.sha256(raw).hexdigest()
        or provenance.run_id != events[0].payload.run_id
        or provenance.event_count != len(events)
    ):
        raise RecordError("exporter provenance does not match the recorded log")
    return events, provenance


def total(ticks: list[UsageTick], metric: str) -> int | float | None:
    values = [cast(int | float | None, getattr(tick, metric)) for tick in ticks]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def summarize_agent(
    agent_id: str, events: list[ServerEvent], run: RunStarted, fake: bool
) -> AgentManifest:
    statuses = [event.payload for event in events if event.type == "agent.status"]
    profiles = {profile.id: profile for profile in run.models}
    profile: ModelProfile | None = profiles.get(statuses[0].model) if statuses else None
    if any(
        status.model != statuses[0].model or status.slot != statuses[0].slot for status in statuses
    ):
        raise RecordError(f"{agent_id}: model profile or slot changed within the log")
    ticks = [event.payload for event in events if event.type == "usage.tick"]
    resolutions = [event.payload for event in events if event.type == "change.resolved"]
    errors = [event.payload for event in events if event.type == "error"]
    inputs = cast(int | None, total(ticks, "input_tokens"))
    cached = cast(int | None, total(ticks, "cached_tokens"))
    return AgentManifest(
        agent_id=agent_id,
        slot=statuses[0].slot if statuses else None,
        requested_profile=profile.id if profile else None,
        requested_model=FAKE if fake else profile.model if profile else None,
        provider="fake" if fake else profile.provider if profile else None,
        reported_models=dict(sorted(Counter(tick.model for tick in ticks if tick.model).items())),
        unknown_model_calls=sum(tick.model is None or not tick.model for tick in ticks),
        mode=None if fake else profile.reasoning if profile else None,
        prompt_version=run.prompt_version,
        limits=run.limits.model_dump(),
        final_status=statuses[-1].state if statuses else None,
        decision=resolutions[-1].decision if resolutions else None,
        merge_result=resolutions[-1].merge if resolutions else None,
        errors=len(errors),
        fatal_errors=sum(error.fatal for error in errors),
        first_event_time=min(event.ts for event in events),
        last_event_time=max(event.ts for event in events),
        calls=len(ticks),
        input_tokens=inputs,
        output_tokens=cast(int | None, total(ticks, "output_tokens")),
        cached_tokens=cached,
        cached_input_share=cached / inputs if inputs and cached is not None else None,
        cost_usd=total(ticks, "cost_usd"),
        unknown_usage_calls=sum(
            any(getattr(tick, key) is None for key in METRICS) for tick in ticks
        ),
        unknown_calls={key: sum(getattr(tick, key) is None for tick in ticks) for key in METRICS},
    )


def build_manifest(log: Path, *, expect_b: str | None = None, real: bool = False) -> Manifest:
    events, provenance = read_log(log)
    first = events[0]
    assert first.type == "run.started"
    run = first.payload
    if len({profile.id for profile in run.models}) != len(run.models):
        raise RecordError("duplicate model profile ids in run.started")
    fake = provenance.exporter == "oversight.fake_recording.v1" or any(
        profile.provider == "fake" for profile in run.models
    )
    grouped: dict[str, list[ServerEvent]] = {}
    for event in events:
        agent_id = cast(str | None, getattr(event.payload, "agent_id", None))
        if agent_id is not None:
            grouped.setdefault(agent_id, []).append(event)
    manifest = Manifest(
        run_id=run.run_id,
        recording_kind=FAKE if fake else "real recorded run",
        provenance=provenance,
        first_event_time=min(event.ts for event in events),
        last_event_time=max(event.ts for event in events),
        merge_mode=run.merge_mode.mode if run.merge_mode else None,
        agents=[summarize_agent(key, value, run, fake) for key, value in sorted(grouped.items())],
    )
    if fake and (real or provenance.exporter == "oversight.record_run.v1"):
        manifest.checks.append("scripted fake model cannot be presented as a real Meta run")
    profiles = {profile.id: profile for profile in run.models}
    for agent in manifest.agents:
        profile = profiles.get(agent.requested_profile or "")
        if profile is None:
            manifest.checks.append(f"{agent.agent_id}: requested model is unknown")
            continue
        for model in agent.reported_models:
            if model != profile.model:
                manifest.checks.append(
                    f"{agent.agent_id}: reported model {model!r} "
                    f"differs from requested {profile.model!r}"
                )
    if expect_b is not None:
        agents_b = [agent for agent in manifest.agents if agent.slot == "B"]
        if not agents_b:
            manifest.checks.append("--expect-b: no recorded agent in slot B")
        for agent in agents_b:
            if (
                fake
                or agent.requested_model != expect_b
                or any(model != expect_b for model in agent.reported_models)
            ):
                manifest.checks.append(
                    f"--expect-b: {agent.agent_id} requested or reported "
                    f"a different model than {expect_b!r}"
                )
            elif not agent.reported_models or agent.unknown_model_calls:
                manifest.checks.append(f"--expect-b: {agent.agent_id} reported model is unknown")
    return manifest


def cell(value: object) -> str:
    return ("unknown" if value is None else str(value)).replace("|", "\\|").replace("\n", " ")


def markdown(manifest: Manifest) -> str:
    rows = [
        "# Model execution manifest",
        "",
        f"Recording: **{manifest.recording_kind}**. Run: `{manifest.run_id}`.",
        f"Event times are UTC. Merge mode: {cell(manifest.merge_mode)}.",
        "Model ids below are logged values; fake runs contain simulated calls and costs.",
        "",
        "| Agent | Requested / provider | Reported model (calls) | Mode / prompt | Outcome "
        "| Calls (unknown usage / model) | Input / output / cached | Cached-input share "
        "| Cost USD | Event times (UTC) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for agent in manifest.agents:
        models = ", ".join(f"{model} ({count})" for model, count in agent.reported_models.items())
        rows.append(
            "| "
            + " | ".join(
                cell(value)
                for value in [
                    f"{agent.slot or 'unknown'} / {agent.agent_id}",
                    f"{cell(agent.requested_model)} / {cell(agent.provider)}",
                    models or None,
                    f"{cell(agent.mode)} / {agent.prompt_version}",
                    " / ".join(
                        cell(v) for v in (agent.final_status, agent.decision, agent.merge_result)
                    ),
                    f"{agent.calls} ({agent.unknown_usage_calls} / {agent.unknown_model_calls})",
                    " / ".join(
                        cell(v)
                        for v in (agent.input_tokens, agent.output_tokens, agent.cached_tokens)
                    ),
                    f"{agent.cached_input_share:.1%}"
                    if agent.cached_input_share is not None
                    else None,
                    agent.cost_usd,
                    f"{agent.first_event_time} - {agent.last_event_time}",
                ]
            )
            + " |"
        )
    if manifest.agents:
        limits = manifest.agents[0].limits
        rows.extend(
            ["", "Limits: " + ", ".join(f"{key}={value}" for key, value in limits.items()) + "."]
        )
    if manifest.checks:
        rows.extend(["", "Checks failed:", "", *(f"- {check}" for check in manifest.checks)])
    return "\n".join(rows) + "\n"


def write_manifest(log: Path, manifest: Manifest, out: Path | None = None) -> tuple[Path, Path]:
    """OUT is a prefix (optionally ending in .json); never overwrite an input or receipt."""
    prefix = out if out is not None else log.with_name(log.name + ".manifest")
    if prefix.suffix == ".json":
        prefix = prefix.with_suffix("")
    targets = (Path(str(prefix) + ".json"), Path(str(prefix) + ".md"))
    for target in targets:
        for source in (log, provenance_path(log)):
            if target.resolve() == source.resolve() or (
                target.exists() and target.samefile(source)
            ):
                raise RecordError("manifest output matches an input file")
    targets[0].parent.mkdir(parents=True, exist_ok=True)
    targets[0].write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    targets[1].write_text(markdown(manifest), encoding="utf-8")
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--out", type=Path, help="output prefix, optionally ending in .json")
    parser.add_argument(
        "--expect-b", help="require this exact requested and reported model for slot B"
    )
    parser.add_argument(
        "--real", action="store_true", help="require a real recording, refusing fake runs"
    )
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(args.log, expect_b=args.expect_b, real=args.real)
        targets = write_manifest(args.log, manifest, args.out)
    except RecordError as error:
        print("manifest refused: " + " ".join(str(error).splitlines()), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        # Invalid input may contain arbitrary text; never echo its contents or a traceback.
        print("manifest refused: invalid log, provenance or output", file=sys.stderr)
        return 1
    for check in manifest.checks:
        print("manifest check: " + " ".join(check.splitlines()), file=sys.stderr)
    print(json.dumps([str(target) for target in targets]))
    return 1 if manifest.checks else 0


if __name__ == "__main__":
    sys.exit(main())
