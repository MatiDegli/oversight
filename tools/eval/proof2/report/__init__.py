"""Read one run without executing checks or calling a model."""

from hashlib import sha256
from pathlib import Path

from oversight.events import LIST_PRICES

from .arms import build_arms
from .inputs import load_inputs
from .measures import measures
from .output import write_lines
from .spend import add_spend
from .timing import add_timing

__all__ = ["write_report"]


def write_report(log: Path, results: Path, out: Path | None = None) -> None:
    inputs = load_inputs(log, results)
    started = next((e.payload for e in inputs.events if e.type == "run.started"), None)
    meta = dict(
        record="meta",
        schema=1,
        **{k: getattr(started, k, None) for k in ("run_id", "contract_version", "prompt_version")},
        through_seq=inputs.supervision.through_seq,
        changes=len(inputs.supervision.changes),
        revisions=len(inputs.proposals),
        with_hidden=len(inputs.results),
        log_sha256=sha256(log.read_bytes()).hexdigest(),
        results_sha256=sha256(results.read_bytes()).hexdigest(),
        price_source=LIST_PRICES.source_url,
        price_retrieved=LIST_PRICES.retrieved,
    )
    revisions, changes, arms = build_arms(inputs)
    add_spend(inputs, changes, arms)
    run = add_timing(inputs, revisions, changes, arms)
    not_built = [dict(record=name, status="not_built") for name in ("E3", "E4", "E6", "E7")]
    write_lines(
        [meta, *revisions, *changes, *measures(inputs, revisions), *arms, run, *not_built], out
    )
