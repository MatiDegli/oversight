"""Vite's build gate: emit only a verified recording, never raw unvalidated event text."""

import argparse
import json
import sys
from pathlib import Path

from recording import RecordError, load_recording


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args(argv)
    try:
        bundled = load_recording(args.log)
    except RecordError as error:
        print(f"recorded replay refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(bundled))
    return 0


if __name__ == "__main__":
    sys.exit(main())
