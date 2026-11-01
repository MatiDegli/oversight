"""Fake rules recorder and three-start check, sharing demo-check's lifecycle checks.

Only harness-owned scratch stores are exported. Settings are explicit and all profiles are
fake. Host isolation is checked by normal startup; unavailable C fails selection with its reason.
"""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import httpx
import uvicorn

import demo_check as demo
from oversight.app import create_app
from record_run import latest_store, record_run
from rules_scenario import DEADLINE, Events, merge, needs_you, revert, select, settings

OUT = demo.REPO_ROOT / "fixtures/events/scripted-rules.jsonl"


async def scenario(base: str, reviewer: Path) -> tuple[str, str]:
    async with (
        asyncio.timeout(4 * DEADLINE),
        httpx.AsyncClient(
            base_url=base, timeout=DEADLINE, limits=httpx.Limits(max_keepalive_connections=0)
        ) as client,
        Events(client) as events,
    ):
        initial = (await client.get("/graph")).json()["graph"]
        await select(client, events)
        resolution = await merge(client, events)
        await revert(client, events, resolution)
        await needs_you(client, events, reviewer)
        return initial["version"], initial["commit"]


def run_once(number: int, scratch: Path, runtime: Path) -> tuple[str, str]:
    """Normal shutdown, descendants and runtime lock use the existing demo harness authority."""
    port = demo.smoke.free_port()
    parent = os.getpid()
    log = scratch / f"rules-{number}.log"
    with log.open("wb") as output:
        child = subprocess.Popen(
            [sys.executable, __file__, "--serve", "--runtime", str(runtime), "--port", str(port)],
            cwd=demo.REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            # Reuse the established parent-death and cleanup lifecycle, without a second copy.
            preexec_fn=lambda: demo._die_with_parent(parent),  # pyright: ignore[reportPrivateUsage]
        )
    try:
        base = demo.smoke.base_url(port)
        demo.smoke.wait_for_health(base, demo.START_DEADLINE, child)
        initial = asyncio.run(scenario(base, runtime.parent / "rules-reviewer.json"))
    except Exception as error:
        raise RuntimeError(f"rules start {number}: {error}\n{log.read_text()}") from error
    finally:
        demo.stop_server(child)
        left = demo.stragglers(child.pid, os.getpid())
        for pid in left:
            demo._kill(pid)  # pyright: ignore[reportPrivateUsage]
    assert child.returncode in demo.GRACEFUL_EXITS, f"server exited {child.returncode}"
    assert not left, f"surviving processes: {left}"
    assert demo.lock_is_free(runtime), "runtime lock remains held"
    print(f"rules-check: start {number}: merge, Revert, Needs you; cleanup: ok", flush=True)
    return initial


def run_checks(*, runs: int = 3, out: Path | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="oversight-rules-") as scratch, demo.adopting_orphans():
        root = Path(scratch).resolve()
        runtime = root / ".runtime"
        first = None
        for number in range(1, runs + 1):
            initial = run_once(number, root, runtime)
            if first is None:
                first = initial
            assert initial == first, "initial graph/commit differs: workspace did not reset"
        if out is not None:
            result = record_run(latest_store(runtime / "runs"), out, fake_fixture=True)
            print(f"Recorded {result.count} committed fake-only events to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        assert args.runtime is not None and args.port is not None
        config = replace(settings(args.runtime), port=args.port)
        uvicorn.run(create_app(config), host=config.host, port=config.port, proxy_headers=False)
    else:
        run_checks(runs=1 if args.record else 3, out=OUT if args.record else None)


if __name__ == "__main__":
    main()
