"""A broken editable-review path must fail the demo checker, never certify a partial save."""

import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from tools.tests.test_demo_check import A, event_server, sample

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check_review as review
import smoke
from demo_check_io import EventStream, OverallClock, Reply
from oversight_contracts.commands import FileContent

MAIN = "def apply_coupon(total, code):\n    return total\n"
AGENT = MAIN.replace("    return", f'    """{review.LONG_DOC}"""\n    return')
EDITED = AGENT + review.USER_EDIT
REVISED = EDITED.replace(review.LONG_DOC, review.SHORT_DOC)
NODE_ID = f"shop.pricing:{review.USER_NODE}"


def file(content: str, agent_id: str | None = A.agent_id) -> FileContent:
    return FileContent(
        path=review.PATH,
        content=content,
        hash=sha256(content.encode()).hexdigest(),
        agent_id=agent_id,
    )


def pending(kind: str, seq: int, **kwargs: Any) -> dict[str, Any]:
    event = sample(kind, seq, **kwargs)
    snapshot = sample("graph.snapshot", 1)["payload"]
    node = next(n for n in snapshot["nodes"] if n["kind"] == "function")
    node.update(id=NODE_ID, name=review.USER_NODE, path=review.PATH)
    event["payload"]["graph"]["nodes_added"] = [node]
    if kind == "change.updated":
        event["payload"]["file"].update(original=MAIN, modified=EDITED)
        event["payload"]["node_ids"] = [NODE_ID]
    return event


@pytest.mark.parametrize("broken", [None, "author", "content", "node", "tests", "worktree", "main"])
def test_review_save_verdict(monkeypatch: pytest.MonkeyPatch, broken: str | None) -> None:
    updated = pending("change.updated", 1, author="user")
    proposed = pending("change.proposed", 2, revision=2)
    if broken == "author":
        updated["payload"]["author"] = "agent"
    elif broken == "content":
        updated["payload"]["file"]["modified"] = AGENT
    elif broken == "node":
        proposed["payload"]["graph"]["nodes_added"] = []
    elif broken == "tests":
        proposed["payload"]["tests"]["outcome"] = "failed"
    reads = iter(
        [
            file(MAIN, None),
            file(AGENT),
            file(AGENT if broken == "worktree" else EDITED),
            file(EDITED if broken == "main" else MAIN, None),
        ]
    )
    monkeypatch.setattr(review, "read_file", lambda *a: next(reads))
    puts: list[dict[str, Any]] = []

    def request(*args: Any, **kwargs: Any) -> Reply:
        puts.append(kwargs["put"])
        return Reply(200, {}, json.dumps({"path": review.PATH, "hash": file(EDITED).hash}).encode())

    monkeypatch.setattr(review, "request", request)
    with event_server([json.dumps(e) for e in (updated, proposed)]) as base:
        stream = EventStream(base)
        try:
            if broken:
                with pytest.raises(smoke.SmokeError, match="review"):
                    review.save_review(base, stream, OverallClock(), A, _proposal(), 0)
            else:
                result, saved = review.save_review(base, stream, OverallClock(), A, _proposal(), 0)
                assert result.payload.revision == 2 and saved.content == EDITED
            assert puts == [
                {
                    "path": review.PATH,
                    "content": EDITED,
                    "base_hash": file(AGENT).hash,
                    "agent_id": A.agent_id,
                }
            ]
        finally:
            stream.close()


def _proposal() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(payload=SimpleNamespace(revision=1))


@pytest.mark.parametrize(
    "broken", [None, "accepted_stale", "wrong_error", "early_read", "lost_user", "edit_failed"]
)
def test_revision_requires_stale_failures_then_read_and_preserves_user_edit(
    monkeypatch: pytest.MonkeyPatch, broken: str | None
) -> None:
    stale = {
        "tool": "edit_file",
        "result": "error",
        "error": f"{review.PATH} changed since you last read it; read it again",
    }
    events = [
        sample("agent.step", 1, **stale),
        sample("agent.step", 2, **stale),
        sample("agent.step", 3, tool="read_file"),
        pending("change.updated", 4, author="agent"),
        sample("agent.step", 5, tool="edit_file"),
    ]
    events[3]["payload"]["file"]["modified"] = REVISED
    if broken == "accepted_stale":
        events[0]["payload"].update(result="ok", error=None)
    elif broken == "wrong_error":
        events[0]["payload"]["error"] = "old_text does not appear"
    elif broken == "early_read":
        events[0]["payload"]["tool"] = "read_file"
    elif broken == "lost_user":
        events[3]["payload"]["file"]["modified"] = REVISED.replace(review.USER_EDIT, "")
    elif broken == "edit_failed":
        events[4]["payload"].update(result="error", error="write failed")
    refused: list[str] = []
    monkeypatch.setattr(review, "refuse_save", lambda *a: refused.append(a[3].agent_id))
    monkeypatch.setattr(review, "read_file", lambda *a: file(REVISED))
    with event_server([json.dumps(e) for e in events]) as base:
        stream = EventStream(base)
        try:
            if broken:
                with pytest.raises(smoke.SmokeError, match="A"):
                    review.revision_steps(base, stream, OverallClock(), A, 0, file(EDITED))
            else:
                review.revision_steps(base, stream, OverallClock(), A, 0, file(EDITED))
                assert refused == [A.agent_id]
        finally:
            stream.close()


@pytest.mark.parametrize("broken", [None, "status", "code", "message", "main_write", "agent_write"])
def test_working_save_refusal_requires_clear_error_and_unchanged_files(
    monkeypatch: pytest.MonkeyPatch, broken: str | None
) -> None:
    status = 200 if broken == "status" else 409
    error = {
        "code": "stale_file" if broken == "code" else "wrong_state",
        "message": "no"
        if broken == "message"
        else f"{A.change_id} is working; a save in review needs ready",
    }
    monkeypatch.setattr(
        review, "request", lambda *a, **kw: Reply(status, {}, json.dumps({"error": error}).encode())
    )
    reads = iter(
        [
            file(MAIN, None),
            file("changed" if broken == "main_write" else MAIN, None),
            file("changed" if broken == "agent_write" else EDITED),
        ]
    )
    monkeypatch.setattr(review, "read_file", lambda *a: next(reads))
    if broken:
        with pytest.raises(smoke.SmokeError, match="review"):
            review.refuse_save("", OverallClock(), A, file(EDITED))
    else:
        review.refuse_save("", OverallClock(), A, file(EDITED))
