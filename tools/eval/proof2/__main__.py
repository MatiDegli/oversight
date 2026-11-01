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
    checker.add_argument("--base", type=Path)
    validator = commands.add_parser("validate")
    validator.add_argument("--source", type=Path, required=True)
    reporter = commands.add_parser("report")
    reporter.add_argument("--log", type=Path, required=True)
    reporter.add_argument("--results", type=Path, required=True)
    reporter.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = asyncio.run(check(args.task, args.tree, base=args.base))
            print(json.dumps(result))
            return int(result["exit_code"] != 0)
        if args.command == "report":
            from .report import write_report

            write_report(args.log, args.results, args.out)
            return 0
        results = asyncio.run(validate(args.source))
        return int(any(not row["ok"] for row in results))
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
