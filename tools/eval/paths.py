"""Canonical paid output guard without product app/settings imports."""

from pathlib import Path

from tools.eval.tasks import REPO, BenchError

PAID_OUTPUT = REPO / ".eval-output" / "bench" / "results.jsonl"


def output_path(out: Path, *, paid: bool, canonical: Path = PAID_OUTPUT) -> Path:
    out = out.resolve()
    if paid:
        if out != canonical.resolve():
            raise BenchError(f"paid --out must resolve to {canonical}")
        # Older directories cannot silently disappear from cumulative accounting.
        older = [
            p
            for p in canonical.parent.parent.rglob("ledger.json")
            if not p.is_relative_to(canonical.parent)
            and p.parent != canonical.parent.parent / "lead"
        ]
        if older:
            raise BenchError("legacy bench ledgers need consolidation before paid runs")
    return out
