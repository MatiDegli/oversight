"""Shared stage-clock oracle. Regenerate only from the backend fold, never by hand."""

import json
import sys
from pathlib import Path
from typing import Any

from oversight.events import fold_stages, read_jsonl

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures/events"
GOLDEN = FIXTURES / "stage-clocks.golden.json"
REGENERATE = "uv run python backend/tests/events/test_stage_golden.py --write"


def stage_entries() -> list[dict[str, Any]]:
    entries = []
    for name in ["recorded-demo", "stage-clocks"]:
        events = read_jsonl(FIXTURES / f"{name}.jsonl")
        changes = sorted(
            {
                change_id
                for event in events
                if isinstance(change_id := getattr(event.payload, "change_id", None), str)
            }
        )
        for change_id in changes:
            for n in range(1, len(events) + 1):
                stages = fold_stages(events[:n], change_id)
                entries.append(
                    dict(
                        log=name,
                        change_id=change_id,
                        n=n,
                        stages=stages.model_dump(mode="json") if stages is not None else None,
                    )
                )
    return entries


def test_shared_stage_golden():
    # read_jsonl above validates every handwritten envelope against ServerEvent as well.
    expected = json.loads(GOLDEN.read_text())
    actual = stage_entries()
    assert len(actual) == len(expected), f"Regenerate: {REGENERATE}"
    for entry, golden in zip(actual, expected, strict=True):
        assert entry == golden, (
            f"Stage parity at {entry['log']}/{entry['change_id']}, prefix {entry['n']}. "
            f"Regenerate: {REGENERATE}"
        )


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        raise SystemExit(f"Regenerate: {REGENERATE}")
    GOLDEN.write_text(json.dumps(stage_entries(), indent=2) + "\n")
