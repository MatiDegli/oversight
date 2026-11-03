"""UI-journey evidence, not trust evidence: real App against the scripted fake/C shop."""

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from tools.preview_acceptance_checks import candidate, free_port, released, server, settings
from tools.preview_chat_fixture import PROVENANCE, seed_preview_chat


async def bounded(argv: list[str], env: dict[str, str], seconds: float) -> int:
    process = await asyncio.create_subprocess_exec(*argv, env=env)
    try:
        async with asyncio.timeout(seconds):
            return await process.wait()
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await asyncio.wait_for(process.wait(), 5)


async def execute(root: Path) -> int:
    await asyncio.to_thread(lambda: root.mkdir(parents=True, exist_ok=True))
    backend, vite = free_port(), free_port()
    pool = tuple({free_port() for _ in range(24)} - {backend, vite})[:16]
    config = settings(root / "browser-backend" / ".runtime", backend, vite=vite, pool=pool)
    async with asyncio.timeout(600), server(config) as running:
        target = await candidate(running)
        seeded = await seed_preview_chat(running.app.state.live.store, target)
        evidence = root / "candidate.json"
        evidence.write_text(json.dumps(target.opening()) + "\n")
        env = {k: v for k, v in os.environ.items() if k not in {"META_API_KEY", "BASETEN_API_KEY"}}
        env.update(
            OVERSIGHT_PREVIEW_CHAT_ONLY="1",
            OVERSIGHT_PORT=str(backend),
            OVERSIGHT_VITE_PORT=str(vite),
            OVERSIGHT_PREVIEW_PORTS=",".join(map(str, pool)),
            OVERSIGHT_PREVIEW_ACCEPTANCE_CANDIDATE=str(evidence),
        )
        try:
            result = await bounded(
                [
                    "pnpm",
                    "-C",
                    "frontend",
                    "exec",
                    "vitest",
                    "related",
                    "--run",
                    "--maxWorkers",
                    "2",
                    "--project",
                    "preview-acceptance",
                    "src/app/preview/PreviewAcceptance.browser.test.ts",
                    "--testNamePattern",
                    "App Chat journey",
                ],
                env,
                540,
            )
        except TimeoutError:
            result = 124
        live = running.app.state.live
        before = next(
            e.payload.base_commit for e in live.store.committed_events() if e.type == "run.started"
        )
        # A browser failure can leave ownership: verified drain still precedes inspection/reject.
        await live.previews.drain("closed")
        assert await live.workspace.main.head() == before
        response = await running.client.post(
            f"/changes/{target.change_id}/reject",
            json={
                **target.decision(),
                "comment": "Chat UI-journey cleanup only.",
            },
        )
        assert response.status_code == 202
        async with asyncio.timeout(15):
            while (  # noqa: ASYNC110 - committed event predicate
                any(
                    e.type == "change.resolved"
                    and e.payload.change_id == target.change_id
                    and e.payload.decision == "rejected"
                    for e in live.store.committed_events()
                )
                is False
            ):
                await asyncio.sleep(0.02)
    await released(live.workspace, pool)
    (root / "browser-cleanup.json").write_text(
        json.dumps(
            {
                "exit": result,
                "main_unchanged": True,
                "cleanup": "registry/listener/lock released",
                "completedUTC": datetime.now(UTC).isoformat(),
                "candidate": asdict(target),
                "windowsBrowser": False,
                "provenance": PROVENANCE,
                "fixture_seqs": seeded,
            },
            indent=2,
        )
        + "\n"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=Path(".shots/preview-chat/real-local"))
    return asyncio.run(execute(parser.parse_args().evidence.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
