"""Four-arm frozen-patch bench. Defaults to provider-free cost estimation."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from tools.eval.behavior.catalog import check_baseline, load_patches
from tools.eval.detection.config import (
    ARMS,
    PROTOCOL,
    Arm,
    Assumptions,
    paid_gate,
    projection,
    rates_for,
)
from tools.eval.detection.engine import CANONICAL_OUTPUT, run_plan
from tools.eval.detection.labels import load_labels
from tools.eval.detection.reporting import paid_run_gate, sensitivity, summarize, superseded_pilots
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger, State
from tools.eval.paths import output_path
from tools.eval.tasks import BenchError


def history(out: Path, cap: float, *, ready: bool = True) -> State | None:
    path = out.parent / "ledger.json"
    if not path.exists():
        if out.exists():
            raise BenchError("results exist without their ledger")
        return None
    state = State.model_validate_json(path.read_bytes())
    if state.results_file != out.name or state.cap_usd != cap:
        raise BenchError("ledger output or cap changed")
    if ready and any(r.unknown or r.status != "finished" for r in state.records):
        raise BenchError("unknown cost or unsettled reservation blocks projection/execution")
    return state


def provider_factory(env_file: Path):
    """Called only after all gates and a durable reservation; never on projection or fake paths."""
    from oversight.agents import (
        BASETEN_BASE_URL,
        META_BASE_URL,
        MUSE_SPARK,
        MUSE_SPARK_OPEN,
    )
    from oversight.settings import read_env_file

    credentials = read_env_file(env_file)

    from tools.eval.detection.transport import TransportClient

    def create(arm: Arm) -> TransportClient:
        profile, url, key = (
            (MUSE_SPARK, META_BASE_URL, "META_API_KEY")
            if arm.profile == "meta"
            else (MUSE_SPARK_OPEN, BASETEN_BASE_URL, "BASETEN_API_KEY")
        )
        if not credentials.get(key):
            raise BenchError(f"{arm.profile}: unavailable credentials in explicit env file")
        assert arm.max_output_tokens is not None
        return TransportClient(
            profile,
            base_url=url,
            api_key=credentials[key],
            max_output_tokens=arm.max_output_tokens,
        )

    return create


def parser_for() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=["dry-run", "pilot", "run", "report", "sensitivity"],
        default="dry-run",
    )
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--labels-sha256")
    parser.add_argument("--paid", action="store_true")
    parser.add_argument("--fake", action="store_true")
    parser.add_argument(
        "--fake-choice", choices=["asked_for", "not_asked_for", "cannot_tell", "unavailable"]
    )
    parser.add_argument("--out", type=Path, default=CANONICAL_OUTPUT)
    parser.add_argument("--against", type=Path)
    parser.add_argument("--cap-usd", type=float, default=20)
    parser.add_argument("--meta-input-usd", type=float)
    parser.add_argument("--meta-output-usd", type=float)
    parser.add_argument("--baseten-input-usd", type=float)
    parser.add_argument("--baseten-output-usd", type=float)
    parser.add_argument("--input-tokens", type=int)
    parser.add_argument("--output-tokens", type=int)
    parser.add_argument("--calls", type=int)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--demo-python", type=Path)
    parser.add_argument("--env-file", type=Path)
    return parser


def validated_labels(args):
    if args.command not in {"run", "report", "sensitivity"}:
        return None
    if args.labels is None or args.labels_sha256 is None:
        raise BenchError("--labels and --labels-sha256 are required for scored execution")
    labels = load_labels(args.labels, args.labels_sha256)
    outputs = [args.out]
    if args.command == "sensitivity" and args.against is not None:
        outputs.append(args.against)
    for out in outputs:
        forbidden = [out, out.parent / "ledger.json", out.parent / "ledger.lock"]
        if args.labels.resolve() in {
            p.resolve() for p in forbidden
        } or args.labels.resolve().is_relative_to((out.parent / "detection").resolve()):
            raise BenchError("labels must be outside writable accounting paths")
    return labels


def configured_rates(args):
    meta = (args.meta_input_usd, args.meta_output_usd)
    baseten = (args.baseten_input_usd, args.baseten_output_usd)
    meta = meta if None not in meta else None
    baseten = baseten if None not in baseten else None
    if args.command != "dry-run" and not args.fake:
        rates = paid_gate(args.paid, args.cap_usd, meta, baseten)
        output_path(args.out, paid=True, canonical=CANONICAL_OUTPUT)
        if not (args.out.parent / "ledger.json").is_file():
            raise BenchError("paid detection needs an existing consolidated canonical ledger")
        return rates
    if args.fake:
        if args.command == "dry-run" or args.fake_choice is None:
            raise BenchError("fake execution requires pilot/run and an explicit --fake-choice")
        if args.out.resolve().parent == CANONICAL_OUTPUT.resolve().parent:
            raise BenchError("fake execution requires separate --out outside the paid directory")
        return {p: Tariff(input=0, output=0) for p in ("meta", "baseten")}
    if args.out.resolve() != CANONICAL_OUTPUT.resolve():
        raise BenchError("dry-run must include the canonical paid ledger")
    output_path(args.out, paid=True, canonical=CANONICAL_OUTPUT)
    return rates_for(meta, baseten)


def execute(args, labels, rates, assumptions):
    if args.demo_python is None or not args.demo_python.is_file():
        raise BenchError("explicit --demo-python executable required")
    if not args.fake and (args.env_file is None or args.env_file.name != ".env"):
        raise BenchError("paid execution requires an explicit --env-file pointing to .env")
    if not args.fake and not (args.demo_python.parent.parent / "pyvenv.cfg").is_file():
        raise BenchError("paid product probes need the demo repository's own virtual environment")

    # The closure defers credential reads and SDK construction until a reserved attempt.
    def create(arm):
        return provider_factory(args.env_file)(arm)

    ledger = Ledger(args.out, args.cap_usd, None, multi_tariff=True)
    try:
        result = asyncio.run(
            run_plan(
                ledger,
                rates,
                assumptions,
                args.demo_python,
                pilot=args.command == "pilot",
                labels_hash=labels.sha256 if labels else None,
                fake_choice=args.fake_choice if args.fake else None,
                paid=args.paid,
                repetitions=args.repetitions,
                seed=args.seed,
                factory=None if args.fake else create,
            )
        )
        if labels is not None:
            result["report"] = summarize(
                ledger.state.records,
                labels.answers,
                expected_repetitions=args.repetitions,
                labels_hash=labels.sha256,
            )
        return result
    finally:
        ledger.close()


def compare_passes(args, labels) -> dict:
    if args.against is None:
        raise BenchError("sensitivity requires --against for the second ledger")
    states = [history(out, args.cap_usd, ready=False) for out in (args.out, args.against)]
    for state in states:
        if state is None:
            raise BenchError("sensitivity needs two existing ledgers")
        pins = [
            r.result.detection.labels_sha256
            for r in state.records
            if not r.pilot and r.result and r.result.detection
        ] + [
            p.get("labels_sha256")
            for p in state.plans.values()
            if p.get("kind") == "detection" and not p.get("pilot")
        ]
        if any(pin != labels.sha256 for pin in pins):
            raise BenchError("sensitivity labels pin differs from recorded scoring inputs")
    first, second = states
    assert first is not None and second is not None
    result = sensitivity(first.records, second.records, labels.answers)
    return result | paid_run_gate(result)


def main(argv: list[str] | None = None) -> int:
    args = parser_for().parse_args(argv)
    try:
        # The external byte pin precedes pricing, histories, settings, and runtime setup.
        labels = validated_labels(args)
        if args.fake and args.paid:
            raise BenchError("--fake and --paid are mutually exclusive")
        if args.command == "sensitivity":
            result = compare_passes(args, labels)
        elif args.command == "report":
            state = history(args.out, args.cap_usd, ready=False)
            if state is None:
                raise BenchError("report needs an existing ledger")
            assert labels is not None
            result = summarize(
                state.records,
                labels.answers,
                expected_repetitions=args.repetitions,
                labels_hash=labels.sha256,
            )
        else:
            rates = configured_rates(args)
            assumptions = Assumptions(
                input_tokens=args.input_tokens, output_tokens=args.output_tokens, calls=args.calls
            )
            state = history(args.out, args.cap_usd)
            check_baseline()
            load_patches()
            if args.command == "dry-run":
                result = projection(
                    rates,
                    assumptions,
                    args.repetitions,
                    spent=sum(r.charged_usd for r in state.records) if state else 0,
                    cap=args.cap_usd,
                    pilot_costs={
                        r.arm_id: max(r.charged_usd, r.result.cost_usd)
                        for r in state.records
                        if r.pilot and r.result and r.result.cost_usd != "unknown"
                    }
                    if state
                    else None,
                )
                result["superseded_pilots"] = superseded_pilots(state.records if state else [])
                result["protocol"] = PROTOCOL
                result["configurations"] = [a.configuration(False, rates) for a in ARMS]
                result["ledger"] = str(args.out.parent / "ledger.json")
                result["history_status"] = (
                    "present"
                    if state
                    else (
                        "canonical ledger absent; estimate assumes no recorded history; "
                        "consolidate shared history before paid execution"
                    )
                )
            else:
                result = execute(args, labels, rates, assumptions)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (BenchError, OSError, ValueError, KeyError) as error:
        print(f"Detection bench refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
