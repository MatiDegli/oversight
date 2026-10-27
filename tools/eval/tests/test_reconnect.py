"""A real Oversight fake-model stream cut mid-task, then replaying one duplicate on resume."""

import json
from typing import Any

import pytest
from live_helpers import Scripts, live_server, live_settings  # pyright: ignore[reportMissingImports]
from tools.eval.grade import Tariff
from tools.eval.run import execute
from tools.eval.tasks import load_tasks


@pytest.mark.anyio
async def test_reconnect_deduplicates_and_resumes_without_restarting_agent(tmp_path):
    task = load_tasks()[0]
    before, after = task.files(), task.files(reference=True)
    turns: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "shop/pricing.py"}}]},
        {
            "tool_calls": [
                {
                    "name": "edit_file",
                    "arguments": {
                        "path": "shop/pricing.py",
                        "old_text": before["shop/pricing.py"],
                        "new_text": after["shop/pricing.py"],
                    },
                }
            ]
        },
        {"tool_calls": [{"name": "finish", "arguments": {"summary": "Fixed."}}]},
    ]
    for turn in turns:
        turn["usage"] = {"input_tokens": 100, "output_tokens": 20}
    factory = Scripts([json.dumps({"task": task.prompt, "turns": turns})])
    settings = live_settings(tmp_path)
    out = tmp_path / "results" / "r.jsonl"
    async with live_server(settings, factory) as server:
        original = server.app.middleware_stack
        resumes = []
        cut_seq = 0

        async def interrupted_sse(scope: Any, receive: Any, send: Any) -> None:
            nonlocal cut_seq
            if scope["type"] != "http" or scope["path"] != "/events":
                await original(scope, receive, send)
                return
            last = int(dict(scope["headers"]).get(b"last-event-id", b"0"))
            resumes.append(last)
            if len(resumes) > 2:
                await original(scope, receive, send)
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")],
                }
            )
            # Second connection deliberately repeats the last received usage.tick.
            async for stored in server.store.subscribe(max(0, last - 1)):
                body = f"id: {stored.seq}\ndata: {stored.envelope}\n\n".encode()
                await send({"type": "http.response.body", "body": body, "more_body": True})
                if stored.type == "usage.tick" and not cut_seq:
                    cut_seq = stored.seq
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    return

        server.app.middleware_stack = interrupted_sse
        await execute(
            str(server.client.base_url), "fake", 1, out, [task], Tariff(input=0, output=0)
        )
        row = json.loads(out.read_text())
        assert row["outcome"] == "solved"
        assert row["steps"] == 3 and row["tokens"] == {"input": 300, "output": 60}
        assert resumes[0] == 0 and resumes[1] == cut_seq > 0
        assert factory.calls == 1
        raw = [
            json.loads(line)
            for line in (out.parent / f"events-{server.store.run_id}.jsonl")
            .read_text()
            .splitlines()
        ]
        assert len(raw) == len({(r["run_id"], r["event"]["seq"]) for r in raw})
