"""CLI for independent Proof 2 checks; no model, installer or network client."""

import argparse
import asyncio
import json
from pathlib import Path

from .runner import check, validate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    checker = commands.add_parser("check")
    checker.add_argument("--task", required=True)
    checker.add_argument("--tree", type=Path, required=True)
    validator = commands.add_parser("validate")
    validator.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = asyncio.run(check(args.task, args.tree))
            print(json.dumps(result))
            return int(result["exit_code"] != 0)
        results = asyncio.run(validate(args.source))
        return int(any(not row["ok"] for row in results))
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
