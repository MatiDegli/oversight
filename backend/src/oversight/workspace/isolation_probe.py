"""Optional kernel probe using the real demo interpreter and Workspace.run_tests."""

import asyncio
import json
import platform
from pathlib import Path

from oversight.workspace.core import Workspace
from oversight.workspace.isolation import IsolatedRunner, IsolationUnavailable, unshare_binary
from oversight.workspace.runner import run_process
from oversight.workspace.suite import SuiteUnavailable


async def isolation_probe(runtime: Path, template: Path) -> dict[str, object]:
    """Use a dedicated runtime, with no model calls and no fallback to an open test runner."""
    result: dict[str, object] = {"kernel": platform.release(), "status": "unavailable"}
    try:
        version = await run_process([unshare_binary(), "--version"], cwd=template, timeout_s=5)
        result["util_linux"] = version.stdout.strip()
    except OSError as error:
        result["reason"] = str(error)
        return result
    workspace = Workspace(runtime, template, pytest_pin="9.1.1")
    accepted = asyncio.Event()

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(accepted.wait(), 2)
        result["outside_accepted"] = True
        await workspace.acquire_runtime()
        runner = IsolatedRunner(runtime, port=port)
        await runner.preflight()
        await workspace.reset()
        await workspace.ensure_demo_venv()
        run = await workspace.run_tests(workspace.main, timeout_s=60, spawn=runner)
        result.update(
            status="confirmed" if run.result.outcome == "passed" and not run.problems else "failed",
            entry_confirmed=runner.entry is not None,
            inside_refused=bool(runner.entry and runner.entry.get("refused")),
            entry=runner.entry,
            suite=run.result.model_dump(),
            problems=run.problems,
        )
    except (IsolationUnavailable, SuiteUnavailable, OSError) as error:
        result["reason"] = str(error.cause if isinstance(error, SuiteUnavailable) else error)
    finally:
        workspace.release_runtime()
        server.close()
        await server.wait_closed()
    return result


def print_isolation_probe(runtime: Path, template: Path) -> None:
    print(json.dumps(asyncio.run(isolation_probe(runtime, template)), indent=2))
