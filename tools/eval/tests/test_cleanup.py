"""Fail individual local endpoints, preserving baseline or the unresolved reservation."""

import json
from typing import Any

import pytest
from live_helpers import Scripts, live_server, live_settings  # pyright: ignore[reportMissingImports]
from tools.eval.grade import Tariff
from tools.eval.run import execute
from tools.eval.tasks import BenchError, load_tasks, read_template


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["/graph/rescan", "/reject"])
async def test_setup_failure_rolls_back_and_cleanup_failure_blocks_retry(endpoint, tmp_path):
    task = load_tasks()[0]
    old, new = task.files()["shop/pricing.py"], task.files(reference=True)["shop/pricing.py"]
    turns: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "shop/pricing.py"}}]},
        {
            "tool_calls": [
                {
                    "name": "edit_file",
                    "arguments": {"path": "shop/pricing.py", "old_text": old, "new_text": new},
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
    rates = Tariff(input=0, output=0)
    async with live_server(settings, factory) as server:
        original = server.app.middleware_stack
        failed = False

        async def fail_once(scope, receive, send):
            nonlocal failed
            if (
                scope["type"] == "http"
                and scope["method"] == "POST"
                and scope["path"].endswith(endpoint)
                and not failed
            ):
                failed = True
                await send({"type": "http.response.start", "status": 500, "headers": []})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
            await original(scope, receive, send)

        server.app.middleware_stack = fail_once
        with pytest.raises(BenchError, match="HTTP 500"):
            await execute(str(server.client.base_url), "fake", 1, out, [task], rates)
        ledger = json.loads((out.parent / "ledger.json").read_text())
        if endpoint == "/graph/rescan":
            assert factory.calls == 0 and ledger["records"] == []
            assert not out.read_text()
            for path, text in read_template().items():
                assert (settings.runtime_dir / "workspace" / path).read_text() == text
        else:
            assert factory.calls == 1 and len(out.read_text().splitlines()) == 1
            assert ledger["records"][0]["status"] == "reserved"
            with pytest.raises(BenchError, match="unsettled reservation"):
                await execute(str(server.client.base_url), "fake", 1, out, [task], rates)
            assert factory.calls == 1
