"""Dedicated Oversight children; only Oversight creates provider clients."""

import asyncio
import contextlib
import json
import os
import socket
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
from tools.eval.arms import Arm
from tools.eval.tasks import REPO, BenchError


def environment(arm: Arm, runtime: Path, port: int, cap: float) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "OVERSIGHT_"))
        and key
        not in {
            "META_API_KEY",
            "BASETEN_API_KEY",
            "MUSE_SPARK_INPUT_USD_PER_M",
            "MUSE_SPARK_OUTPUT_USD_PER_M",
        }
    }
    env.update(
        OVERSIGHT_HOST="127.0.0.1",
        OVERSIGHT_PORT=str(port),
        OVERSIGHT_VITE_PORT="",
        OVERSIGHT_RUNTIME_DIR=str(runtime),
        OVERSIGHT_AGENT_MODEL=arm.profile,
        OVERSIGHT_AGENT_MODEL_B=arm.profile,
        OVERSIGHT_HARNESS_PACK=arm.harness_pack,
        OVERSIGHT_CROSS_REVIEW="false",
        OVERSIGHT_BUDGET_USD=str(cap),
    )
    if arm.tariff:
        env.update(
            MUSE_SPARK_INPUT_USD_PER_M=str(arm.tariff.input),
            MUSE_SPARK_OUTPUT_USD_PER_M=str(arm.tariff.output),
        )
    return env


async def stop(child: asyncio.subprocess.Process) -> None:
    if child.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        child.terminate()  # exact child PID; no process-name or process-group kill
    try:
        await asyncio.wait_for(child.wait(), 10)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            child.kill()
        await asyncio.wait_for(child.wait(), 5)


@contextmanager
def runtime_directory(arm: Arm, directory: Path) -> Iterator[Path]:
    if arm.profile == "fake":
        with tempfile.TemporaryDirectory(prefix="bench-", dir=directory) as scratch:
            yield Path(scratch)
    else:
        # Keep the product's ledger and SQLite events, even when SSE or start recovery fails.
        # The cumulative bench ledger remains authoritative; this is billing evidence only.
        yield Path(tempfile.mkdtemp(prefix="server-", dir=directory))


@asynccontextmanager
async def dedicated(
    arm: Arm,
    directory: Path,
    cap: float,
    fake_script: Path | None = None,
    *,
    trace: bool = False,
) -> AsyncIterator[str]:
    if arm.profile == "fake" and fake_script is None:
        raise BenchError("fake runs require --fake-script")
    # Keep the bound socket open until the child exits; no other process can take its port.
    with socket.socket() as sock, runtime_directory(arm, directory) as scratch:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        env = environment(arm, scratch / ".runtime", port, cap)
        if fake_script:
            env["OVERSIGHT_FAKE_SCRIPT"] = str(await asyncio.to_thread(fake_script.resolve))
        if trace:
            env["OVERSIGHT_EVAL_TRACE_DIR"] = str(directory)
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tools.eval.server",
            arm.model_dump_json(),
            str(sock.fileno()),
            pass_fds=(sock.fileno(),),
            cwd=REPO,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                async with asyncio.timeout(60):
                    while True:
                        if child.returncode is not None:
                            raise BenchError(f"Oversight child exited with {child.returncode}")
                        try:
                            response = await client.get(url + "/health")
                            if response.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.05)
            yield url
        finally:
            await stop(child)


def serve(arm: Arm, socket_fd: int) -> None:
    """Configure the product in this isolated process, without changing product sources."""
    import uvicorn

    import oversight.app as product
    from oversight.settings import Settings

    # The product currently pins reasoning in its profile table. Override only this child's
    # selected profile; its advertisement and OpenAIClient receive the same exact setting.
    product.PROFILES[arm.profile] = product.PROFILES[arm.profile].model_copy(
        update={"reasoning": arm.reasoning}
    )
    if arm.profile == "fake":
        settings = Settings(
            host="127.0.0.1",
            port=int(os.environ["OVERSIGHT_PORT"]),
            runtime_dir=Path(os.environ["OVERSIGHT_RUNTIME_DIR"]),
            agent_model="fake",
            fake_script=Path(os.environ["OVERSIGHT_FAKE_SCRIPT"]),
            demo_python=Path(sys.executable),
        )
    else:
        # Keys stay in .env and are loaded only by the Oversight process.
        settings = replace(
            Settings.load(),
            fake_script=None,
            fake_script_b=None,
            cross_review=False,
            reviewer_fake_script=None,
            reviewer_fake_b_script=None,
        )
    config = uvicorn.Config(
        product.create_app(settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        timeout_graceful_shutdown=1,
        log_level="error",
    )
    with socket.socket(fileno=socket_fd) as sock:
        uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    serve(Arm.model_validate(json.loads(sys.argv[1])), int(sys.argv[2]))
