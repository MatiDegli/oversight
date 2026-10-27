"""Regenerate the e2e fixture from a live HTTP backend, using only the L2 fake scripts.

Run with make record-level2. IDs, timestamps and event order come from the SQLite exporter.
The child uses a deliberately bounded cone (depth 1, one node) to exercise omitted counts.
No provider credential reaches it. This fixture is never publishable as a real model recording.
"""

import argparse
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import uvicorn

import demo_check as demo
from demo_check_io import EventStream, OverallClock, expect, request
from demo_check_phases import (
    approve_a,
    check_overlap,
    edit_main,
    graph_of,
    request_revision,
    start_agent,
    stop_working_b,
    wait_for,
)
from oversight import app
from oversight.agents import AgentRun, AgentTask, ModelClient, Pricing
from oversight.settings import Settings
from oversight_contracts.commands import FileContent
from record_run import latest_store, record_run

OUT = demo.REPO_ROOT / "fixtures/events/scripted-level2.jsonl"
REVIEW_EDIT = "\n# Reviewed by the developer.\n"


def priced_fake_run(task: AgentTask, *, model: ModelClient, **options: Any) -> AgentRun:
    """Synthetic prices for this recording only; no provider or real credit is involved."""
    if model.profile.provider != "fake":
        raise ValueError("the level 2 recorder only accepts fake models")
    # Large enough for the cards to show distinct cents, including A's extra revision ticks.
    options["pricing"] = Pricing(input_usd=100, output_usd=200, cached_input_usd=100)
    return AgentRun(task, model=model, **options)


def capture(base: str, runtime: Path) -> None:
    clock = OverallClock()
    # Add real callers through the same main-file command, so this small demo exercises both
    # truncation reasons without manufacturing an impact payload or changing the template.
    original = FileContent.model_validate_json(
        expect(request(base, "/files?path=shop/orders.py"), "GET orders").body
    )
    callers = (
        "\n\ndef replay_coupon_preview(total: int) -> int:\n"
        "    return pricing.apply_coupon(total, '')\n"
        "\n\ndef replay_checkout_total(total: int) -> int:\n"
        "    return replay_coupon_preview(total)\n"
    )
    expect(
        request(
            base,
            "/files",
            put={
                "path": original.path,
                "base_hash": original.hash,
                "content": original.content + callers,
            },
        ),
        "PUT callers",
    )
    initial = graph_of(base, clock)
    stream = EventStream(base, clock=clock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(start_agent, base, clock, slot) for slot in ("A", "B")]
            a, b = [job.result() for job in jobs]
        steps = [wait_for(stream, who, "agent.step", initial.seq) for who in (a, b)]
        proposals = [
            wait_for(stream, who, "change.proposed", initial.seq, revision=1) for who in (a, b)
        ]
        check_overlap(*steps, *proposals)
        for who, proposal in zip((a, b), proposals, strict=True):
            wait_for(stream, who, "agent.status", proposal.seq, state="ready_for_review")
        revising = request_revision(base, stream, clock, a, proposals[0])
        revised = wait_for(stream, a, "change.proposed", revising.seq, revision=2)
        wait_for(stream, a, "agent.status", revised.seq, state="ready_for_review")
        # The review editor writes the idle agent's new side; the server tests/re-proposes it.
        original = FileContent.model_validate_json(
            expect(request(base, f"/files?path=shop/pricing.py&agent={a.agent_id}"), "GET A").body
        )
        expect(
            request(
                base,
                "/files",
                put={
                    "path": original.path,
                    "agent_id": a.agent_id,
                    "base_hash": original.hash,
                    "content": original.content + REVIEW_EDIT,
                },
            ),
            "PUT A review edit",
        )
        edited = wait_for(stream, a, "change.proposed", revised.seq, revision=3)
        stop_working_b(base, stream, clock, b, proposals[1], runtime)
        approve_a(base, stream, clock, a, edited, initial)
        edit_main(base, stream, clock)
    finally:
        stream.close()


def record() -> None:
    with tempfile.TemporaryDirectory(prefix="oversight-record-l2-") as scratch:
        runtime = Path(scratch) / ".runtime"
        port = demo.smoke.free_port()
        with (Path(scratch) / "server.log").open("wb") as output:
            child = subprocess.Popen(
                ["setpriv", "--pdeathsig", "TERM", sys.executable, __file__, "--serve"],
                cwd=demo.REPO_ROOT,
                env=demo.server_env(port, runtime),
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        try:
            base = demo.smoke.base_url(port)
            demo.smoke.wait_for_health(base, demo.START_DEADLINE, child)
            demo.smoke.wait_for_graph(base, demo.START_DEADLINE)
            capture(base, runtime)
        except Exception:
            print((Path(scratch) / "server.log").read_text(), file=sys.stderr)
            raise
        finally:
            demo.stop_server(child)
        result = record_run(latest_store(runtime / "runs"), OUT, fake_fixture=True)
        print(f"Exported {result.count} backend events to {OUT.relative_to(demo.REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        app.LIMITS = app.LIMITS.model_copy(update={"cone_max_depth": 1, "cone_max_nodes": 1})
        settings = Settings.load()
        with patch.object(app, "AgentRun", side_effect=priced_fake_run):
            uvicorn.run(
                app.create_app(settings),
                host=settings.host,
                port=settings.port,
                proxy_headers=False,
                timeout_graceful_shutdown=1,
            )
    else:
        record()


if __name__ == "__main__":
    main()
