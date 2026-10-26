"""Command line: `python -m oversight serve [--reload]`."""

import argparse

import uvicorn

from oversight.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="oversight")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the API")
    serve.add_argument("--reload", action="store_true", help="restart on source changes")
    args = parser.parse_args()

    if args.command == "serve":
        settings = Settings.load()
        uvicorn.run(
            "oversight.app:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
            reload=args.reload,
        )


if __name__ == "__main__":
    main()
