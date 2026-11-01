"""Command line: `python -m oversight serve [--reload]`, `python -m oversight replay <log>` and
`python -m oversight ledger [settle --spent-usd X]`."""

import argparse
import sys
from pathlib import Path

import uvicorn

from oversight.app import ReplayConfig, create_app, ledger_command, pytest_pin
from oversight.settings import REPO_ROOT, Settings
from oversight.workspace import print_isolation_probe


def main() -> None:
    parser = argparse.ArgumentParser(prog="oversight")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the API")
    serve.add_argument("--reload", action="store_true", help="restart on source changes")
    replay = commands.add_parser("replay", help="serve a recorded event log over /events")
    replay.add_argument("log", type=Path, help="a JSONL event log, such as fixtures/events/*.jsonl")
    replay.add_argument("--allow-fake-provider", action="store_true")
    replay.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="1: the log's gaps, each capped at 2 s (default); 2: twice as fast; 0: no pauses",
    )
    ledger = commands.add_parser("ledger", help="print the spend ledger, or settle it")
    ledger_commands = ledger.add_subparsers(dest="ledger_command")
    settle = ledger_commands.add_parser(
        "settle", help="set the spend to Meta's console total and clear unknown calls"
    )
    settle.add_argument("--spent-usd", type=float, required=True, help="US dollars spent so far")
    probe = commands.add_parser("isolation-probe", help="probe network isolation without API calls")
    probe.add_argument(
        "--runtime",
        type=Path,
        default=REPO_ROOT / ".runtime" / "isolation-probe" / ".runtime",
        help="dedicated probe runtime (absolute path ending in .runtime)",
    )
    args = parser.parse_args()
    if args.command == "serve" and args.reload and sys.platform == "win32":
        parser.error("serve --reload is unavailable on Windows; use ordinary serve")
    settings = Settings.load()

    if args.command == "isolation-probe":
        print_isolation_probe(args.runtime, REPO_ROOT / "demo-repo", pytest_pin=pytest_pin())
    elif args.command == "serve":
        uvicorn.run(
            "oversight.app:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
            reload=args.reload,
            proxy_headers=False,
            # Open streams never end by themselves: shutdown gives them a second, then closes them.
            timeout_graceful_shutdown=1,
        )
    elif args.command == "ledger":
        spent = args.spent_usd if args.ledger_command == "settle" else None
        if spent is not None and not (spent >= 0 and spent != float("inf")):
            parser.error("--spent-usd must be a finite amount of 0 or more")
        print(ledger_command(settings, spent))
    elif args.command == "replay":
        if args.speed < 0:
            parser.error("--speed must be 0 or more")
        app = create_app(
            settings,
            ReplayConfig(
                log=args.log, speed=args.speed, allow_fake_provider=args.allow_fake_provider
            ),
        )
        # Open streams never end by themselves, so shutdown gives them a second, then closes them.
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            proxy_headers=False,
            timeout_graceful_shutdown=1,
        )


if __name__ == "__main__":
    main()
