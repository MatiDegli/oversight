"""Person-only live Revert wiring loads authority from the committed run log."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from live_helpers import error_code, live_server, live_settings

from oversight.api.commands import LIVE_ONLY, NOT_BUILT
from oversight.app import MAIN_SUSPECT, ReplayConfig, create_app
from oversight.changes import Person, RevertService
from oversight.settings import REPO_ROOT
from oversight_contracts.events import ChangeResolved

pytestmark = pytest.mark.anyio
URL = "/changes/change-1/revert"


def resolution(*, person=False, change_id="change-1"):
    raw = json.loads((REPO_ROOT / "fixtures/contracts/reserved-1.4.json").read_text())[0]
    text = json.dumps(raw["payload"])
    for name, oid in zip(("base", "head", "main", "candidate", "merged"), "abcde", strict=True):
        text = text.replace(json.dumps(name), json.dumps(oid * 40))
    payload = json.loads(text)
    payload["merge"] = "merged"
    payload["change_id"] = payload["assessment"]["change_id"] = change_id
    if person:
        payload.update(decided_by="person", assessment=None)
    return ChangeResolved.model_validate(payload)


def body(event):
    payload = json.loads(event.envelope)["payload"]
    return dict(
        resolution_seq=event.seq, merge_commit=payload["merge_commit"], main_commit="f" * 40
    )


async def test_revert_route_codes(tmp_path):
    async with live_server(live_settings(tmp_path)) as server:
        row = await server.store.publish(resolution())
        person = await server.store.publish(resolution(person=True))
        other = await server.store.publish(resolution(change_id="change-2"))
        manager = server.live._manager
        assert manager is not None
        before = server.events()
        response = await server.client.post(URL, json={"resolution_seq": 0})
        assert error_code(response) == (422, "invalid_request")
        for seq in (server.store.last_seq + 1, before[0].seq, other.seq):
            response = await server.client.post(URL, json={**body(row), "resolution_seq": seq})
            assert error_code(response) == (404, "not_found")
            assert response.json()["error"]["message"] == (
                f"there is no rules resolution {seq} for change 'change-1'"
            )
        response = await server.client.post(URL, json=body(person))
        assert error_code(response) == (409, "wrong_state")
        assert response.json()["error"]["message"] == "Unverifiable rules target"
        # Revert checks isolation directly; neither cross-review nor the mode latch enables it.
        server.live.workspace.integrity.rules_suspended = True
        response = await server.client.post(URL, json=body(row))
        assert error_code(response) == (409, "wrong_state")
        assert response.json()["error"]["message"] == "Rules authority is suspended until restart"
        server.live.workspace._isolation.invalidate("Lost isolation after startup")
        response = await server.client.post(URL, json=body(row))
        assert error_code(response) == (409, "wrong_state")
        assert "Network isolation C is unavailable" in response.json()["error"]["message"]
        manager._ctx.state.suspect = True
        response = await server.client.post(URL, json=body(row))
        assert error_code(response) == (409, "wrong_state")
        assert response.json()["error"]["message"] == MAIN_SUSPECT
        assert server.events() == before


async def test_revert_route_passes_the_committed_target_and_person(tmp_path, monkeypatch):
    calls = []

    def submit(service, target, command, *, principal):
        calls.append((service, target, command, principal))
        return asyncio.get_running_loop().create_task(asyncio.sleep(0))

    monkeypatch.setattr(RevertService, "submit", submit)
    async with live_server(live_settings(tmp_path)) as server:
        row = await server.store.publish(resolution())
        before = server.events()
        response = await server.client.post(URL, json=body(row))
        assert response.status_code == 202 and response.content == b""
        assert len(calls) == 1
        service, target, command, principal = calls[0]
        assert target.resolution == row and target.change_id == "change-1"
        assert target.run_id == server.live.run_id == service.run_id
        assert principal is Person and command.model_dump() == body(row)
        assert server.live._manager is not None
        assert server.live._manager.merge_mode().mode == "ask_me"
        assert server.events() == before


def test_revert_route_is_live_only(tmp_path):
    path = "/changes/{change_id}/revert"
    assert all(p != path for _, p, _ in NOT_BUILT)
    entries = [(method, p) for method, p, _ in LIVE_ONLY]
    assert entries[entries.index(("PUT", "/merge-mode")) + 1] == ("POST", path)
    log = REPO_ROOT / "fixtures/events/handwritten-level1.jsonl"
    app = create_app(live_settings(tmp_path), ReplayConfig(log=log))
    with TestClient(app, base_url="http://127.0.0.1:8710") as client:
        response = client.post(URL, content=b"not json")
        assert error_code(response) == (501, "not_implemented")
        assert response.json()["error"]["message"] == "Revert is not available in a replay"


def test_proposal_documents_live_revert_command():
    rows = (REPO_ROOT / "docs/proposal.md").read_text().splitlines()
    assert [row for row in rows if row.startswith("| `POST /changes/{id}/revert` |")] == [
        "| `POST /changes/{id}/revert` | "
        "`RevertChange {resolution_seq, merge_commit, main_commit}` | "
        "202 | `not_found`, `wrong_state` |"
    ]


def test_only_http_wiring_reverts_as_person():
    root = Path(__file__).resolve().parents[2] / "src"
    sources = [(p.relative_to(root).as_posix(), p.read_text()) for p in root.rglob("*.py")]
    for pattern in (r"principal=Person", r"\.submit\("):
        assert [path for path, source in sources if re.search(pattern, source)] == [
            "oversight/changes/_revert_port.py"
        ]
    callers = [
        (path, line.strip())
        for path, source in sources
        for line in source.splitlines()
        if re.search(r"\.revert\(", line)
    ]
    assert sorted(callers) == sorted(
        [
            ("oversight/api/commands.py", "await commands.revert(change_id, request)"),
            (
                "oversight/app.py",
                "self._manager.revert(RevertTarget(self.run_id, change_id, resolution), request)",
            ),
            ("oversight/changes/_manager.py", "self._revert.revert(target, command)"),
        ]
    )


async def test_live_manager_composes_one_service(tmp_path):
    async with live_server(live_settings(tmp_path)) as server:
        manager = server.live._manager
        assert manager is not None and manager._revert is not None
        port = manager._revert
        assert port.service.ctx is manager._ctx
        assert port.service.run_id == server.live.run_id
        assert port.service.test_timeout_s == 60
        await server.client.get("/graph")
        assert manager._revert is port
