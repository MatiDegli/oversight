"""Vite's build gate: verify recordings; --fake-fixture verifies test exports separately."""

import argparse
import json
import sys
from pathlib import Path

from recording import RecordError, load_recording


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument(
        "--fake-fixture",
        action="store_true",
        help="validate a scripted test fixture, never a real recording",
    )
    args = parser.parse_args(argv)
    try:
        bundled = load_recording(args.log, fake_fixture=args.fake_fixture)
    except RecordError as error:
        print(f"recorded replay refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(bundled))
    return 0


if __name__ == "__main__":
    sys.exit(main())
