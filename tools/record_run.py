"""Bundle a finished run into the static replay build: `make record RUN=<id>`.

Reads `.runtime/runs/{run_id}.sqlite`, exports it to `fixtures/events/recorded-demo.jsonl`
(by default) and validates the result. It reads a finished run only: it spends no credits
and never calls a model API. A run on the scripted fake model, a log that does not start
with `run.started`, or any string holding an absolute path is refused: the output is
deleted and the exit code is nonzero.
"""

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import TypeAdapter, ValidationError

from oversight.events import export_run, run_store_path
from oversight_contracts.common import RunId
from recording import RecordError, provenance_path, receipt, scan_secrets, validate_text

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "fixtures" / "events" / "recorded-demo.jsonl"
RUNTIME_DIR = REPO_ROOT / ".runtime"
RUN_ID = TypeAdapter(RunId)


@dataclass(frozen=True)
class Recording:
    """What a successful recording prints: the count, the model and the time span."""

    count: int
    model: str
    first_ts: str
    last_ts: str


def latest_store(runs_dir: Path) -> Path:
    """The newest `.runtime/runs/*.sqlite` in `runs_dir`, by modification time."""
    stores = list(runs_dir.glob("*.sqlite"))
    if not stores:
        raise RecordError(f"no recorded runs in {runs_dir}")
    store = max(stores, key=lambda path: (path.stat().st_mtime_ns, path.name))
    return confined_store(store, runs_dir)


def confined_store(store: Path, runs_dir: Path) -> Path:
    """Reject malformed ids and links that escape the run directory."""
    try:
        RUN_ID.validate_python(store.stem)
    except ValidationError:
        raise RecordError("invalid run id: expected run-YYYYMMDD-HHMMSS") from None
    if store.resolve().parent != runs_dir.parent.resolve() / runs_dir.name:
        raise RecordError("run store resolves outside the runs directory")
    return store


def record_run(store_path: Path, out: Path) -> Recording:
    """Export `store_path` to `out`, validate it, and return its summary.

    On any refusal the output is deleted and RecordError is raised.
    """
    if not store_path.exists():
        raise RecordError(f"no such run store: {store_path}")
    metadata = provenance_path(out)
    for target in (out, metadata):
        if target.resolve() == store_path.resolve() or (
            target.exists() and target.samefile(store_path)
        ):
            raise RecordError("output matches the source run store")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix=".record-", dir=out.parent) as scratch:
            candidate = Path(scratch) / "events.jsonl"
            export_run(store_path, candidate)
            text = candidate.read_text(encoding="utf-8")
            events = validate_text(text)
            scan_secrets(candidate)
            marker = provenance_path(candidate)
            marker.write_text(receipt(text, events).model_dump_json() + "\n", encoding="utf-8")
            candidate.replace(out)
            marker.replace(metadata)
    except (RecordError, ValueError, OSError, sqlite3.Error) as error:
        out.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)
        if isinstance(error, RecordError):
            raise
        raise RecordError("could not export or validate the run") from error
    assert events[0].type == "run.started"
    model = events[0].payload.models[0].model if events[0].payload.models else ""
    return Recording(count=len(events), model=model, first_ts=events[0].ts, last_ts=events[-1].ts)


def store_for(run_id: str | None, latest: bool, runtime_dir: Path | None = None) -> Path:
    """The store a command line names: `--latest`, or `.runtime/runs/{run_id}.sqlite`."""
    root = RUNTIME_DIR if runtime_dir is None else runtime_dir
    if latest:
        return latest_store(root / "runs")
    assert run_id is not None
    try:
        RUN_ID.validate_python(run_id)
    except ValidationError:
        raise RecordError("invalid run id: expected run-YYYYMMDD-HHMMSS") from None
    return confined_store(run_store_path(root, run_id), root / "runs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bundle a finished run for the replay build.")
    parser.add_argument("run_id", nargs="?", metavar="RUN_ID", help="the run to record")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help=f"where to write (default: {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--latest", action="store_true", help="record the newest .runtime/runs/*.sqlite"
    )
    args = parser.parse_args(argv)
    if args.latest and args.run_id is not None:
        parser.error("give either RUN_ID or --latest, not both")
    if not args.latest and args.run_id is None:
        parser.error("give RUN_ID or --latest")
    out: Path = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    try:
        recording = record_run(store_for(args.run_id, args.latest), out)
    except RecordError as error:
        print(f"record_run: {error}", file=sys.stderr)
        return 1
    print(
        f"recorded {recording.count} events, model {recording.model}, "
        f"from {recording.first_ts} to {recording.last_ts}, to {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
