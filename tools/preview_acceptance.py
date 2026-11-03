"""Fake-model real-C acceptance with separate runtimes and three consecutive clean starts."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from tools.preview_acceptance_checks import free_port, released, server, settings, story

from oversight.workspace import IsolationUnavailable


async def execute(root: Path, runs: int, *, serve: bool = False, port: int = 8710) -> int:
    if not 1 <= runs <= 20:
        raise ValueError("runs must be between 1 and 20")
    await asyncio.to_thread(lambda: root.mkdir(parents=True, exist_ok=True))
    manifest = root / "starts.json"
    evidence: list[dict[str, object]] = (
        json.loads(manifest.read_text())["attempts"] if manifest.exists() else []
    )
    session = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    clean = 0
    try:
        async with asyncio.timeout(600 if not serve else 7200):
            for attempt in range(1, runs + 1):
                config = settings(
                    root / session / f"runtime-{attempt}" / ".runtime",
                    port if serve else free_port(),
                )
                async with server(config) as running:
                    if serve:
                        print(
                            f"Dedicated scripted fake/C backend ready at {running.origin}",
                            flush=True,
                        )
                        await asyncio.Event().wait()
                    result = await story(running)
                    workspace = running.app.state.live.workspace
                await released(workspace, config.preview_profile.ports)
                clean += 1
                row: dict[str, object] = {
                    "attempt": attempt,
                    "session": session,
                    "consecutive_clean": clean,
                    "cents": result,
                    "cleanup": "registry/listener/lock released",
                    "utc": datetime.now(UTC).isoformat(),
                    "namespaces": [witness.verified() for witness in running.witnesses],
                }
                evidence.append(row)
                print(json.dumps(row), flush=True)
    except IsolationUnavailable as error:
        clean = 0
        evidence.append({"consecutive_clean": 0, "isolation_unavailable": str(error)})
        print(f"Real C unavailable: {error}", flush=True)
        return 2
    except Exception:
        clean = 0
        evidence.append({"consecutive_clean": 0, "failed": True})
        raise
    finally:
        (root / "starts.json").write_text(
            json.dumps({"clean": clean, "attempts": evidence}, indent=2) + "\n"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--evidence", type=Path, default=Path(".shots/preview-acceptance/real-c"))
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--backend-port", type=int, default=8710)
    args = parser.parse_args()
    return asyncio.run(
        execute(args.evidence.resolve(), args.runs, serve=args.serve, port=args.backend_port)
    )


if __name__ == "__main__":
    raise SystemExit(main())
