"""Report dated list-price equivalents for a recorded event log, never a bill."""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from oversight.events import ListPriceFold, LogError, price_usage, read_jsonl


def _line(fold: ListPriceFold) -> str:
    complete = fold.usd is not None
    cost = f"US${fold.usd:.6f}" if complete else f"at least US${fold.known_usd:.6f}"
    parts: list[str] = []
    if fold.unpriced_calls:
        parts.append(f"{fold.unpriced_calls} unpriced")
    if fold.ranged_calls:
        parts.append(f"{fold.ranged_calls} with unreported counts")
    paren = f" ({', '.join(parts)})" if parts else ""
    if not fold.accepted_changes:
        per = "per accepted change unavailable (no accepted changes)"
    else:
        ratio = fold.usd_per_accepted if complete else fold.known_usd_per_accepted
        per = (
            f"per accepted change {'' if complete else 'at least '}US${ratio:.6f} "
            f"over {fold.accepted_changes} accepted"
        )
    line = (
        f"{fold.calls} calls, {fold.priced_calls} priced: {cost} list-price equivalent{paren}; "
        f"{per}; list prices retrieved {fold.retrieved} from {fold.source_url}; "
        f"through seq {fold.through_seq}; coverage {fold.coverage}"
    )
    if fold.coverage == "incomplete":
        line += f": {'; '.join(fold.problems)}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        fold = price_usage(read_jsonl(args.log))
    except (LogError, ValueError, OSError) as error:
        print(f"run cost refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(dataclasses.asdict(fold), sort_keys=True) if args.json else _line(fold))
    return 0


if __name__ == "__main__":
    sys.exit(main())
