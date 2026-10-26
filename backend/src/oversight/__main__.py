"""Command line: `python -m oversight serve [--reload]` and `python -m oversight replay <log>`."""

import argparse
from pathlib import Path

import uvicorn

from oversight.app import ReplayConfig, create_app
from oversight.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="oversight")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the API")
    serve.add_argument("--reload", action="store_true", help="restart on source changes")
    replay = commands.add_parser("replay", help="serve a recorded event log over /events")
    replay.add_argument("log", type=Path, help="a JSONL event log, such as fixtures/events/*.jsonl")
    replay.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="1: the log's gaps, each capped at 2 s (default); 2: twice as fast; 0: no pauses",
    )
    args = parser.parse_args()
    settings = Settings.load()

    if args.command == "serve":
        uvicorn.run(
            "oversight.app:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
            reload=args.reload,
        )
    elif args.command == "replay":
        if args.speed < 0:
            parser.error("--speed must be 0 or more")
        app = create_app(settings, ReplayConfig(log=args.log, speed=args.speed))
        # Open streams never end by themselves, so shutdown gives them a second, then closes them.
        uvicorn.run(app, host=settings.host, port=settings.port, timeout_graceful_shutdown=1)


if __name__ == "__main__":
    main()
