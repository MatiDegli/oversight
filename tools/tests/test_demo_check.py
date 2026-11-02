"""The helpers of tools/demo_check.py. The check itself, three runs of the real server, is
`make demo-check`; these pin the parts that decide its verdict."""

import contextlib
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.platform != "win32":
    import fcntl

import demo_check
import demo_check_phases as phases
import smoke
from oversight_contracts.commands import AgentCreated, GraphResponse

A = AgentCreated(agent_id="agent-1", change_id="change-1", slot="A")
B = AgentCreated(agent_id="agent-2", change_id="change-2", slot="B")

LOG = demo_check.REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"
SLEEPER = "import time; time.sleep(600)"


def test_the_assets_an_index_names_are_found_once_each() -> None:
    html = (
        '<script type="module" crossorigin src="/assets/index-A1.js"></script>'
        '<link rel="stylesheet" href="/assets/index-B2.css">'
        '<link rel="modulepreload" href="/assets/index-A1.js"><a href="/review">not an asset</a>'
    )
    assert demo_check.asset_refs(html) == ["/assets/index-A1.js", "/assets/index-B2.css"]


def test_a_reply_must_have_the_status_and_headers_asked_for() -> None:
    reply = demo_check.Reply(200, {"cache-control": "no-cache", "content-type": "text/html"}, b"")
    assert demo_check.expect(reply, "GET /", cache_control="no-cache", content_type="text/html*")
    with pytest.raises(smoke.SmokeError, match="answered 200, not 404"):
        demo_check.expect(reply, "GET /", 404)
    with pytest.raises(smoke.SmokeError, match="has cache-control: 'no-cache', not 'immutable'"):
        demo_check.expect(reply, "GET /", cache_control="immutable")
    with pytest.raises(smoke.SmokeError, match="has content-type: 'text/html', not 'text/css"):
        demo_check.expect(reply, "GET /", content_type="text/css*")


@contextlib.contextmanager
def event_server(lines: list[str]) -> Iterator[str]:
    """A server whose /events sends `lines` as server-sent events and then closes the stream."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b": ping\r\n\r\n")
            for number, line in enumerate(lines, start=1):
                self.wfile.write(f"id: {number}\r\ndata: {line}\r\n\r\n".encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer((smoke.HOST, 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield smoke.base_url(server.server_address[1])
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def recorded(count: int) -> list[str]:
    return LOG.read_text(encoding="utf-8").splitlines()[:count]


def error_event(seq: int) -> str:
    payload = {"source": "server", "message": "boom", "agent_id": None, "change_id": None}
    envelope = {"seq": seq, "ts": "2026-10-27T14:03:12.000Z", "type": "error"}
    return json.dumps(envelope | {"payload": payload | {"fatal": False}})


def test_the_stream_returns_the_first_event_that_matches() -> None:
    with event_server(recorded(3)) as base:
        stream = demo_check.EventStream(base)
        try:
            found = stream.wait(lambda e: e.type == "graph.snapshot", 10, "a snapshot", after_seq=0)
            assert found.type == "graph.snapshot"
            # An explicit initial epoch permits inspecting earlier events in the same phase.
            again = stream.wait(lambda e: e.type == "run.started", 10, "run.started", after_seq=0)
            assert again.type == "run.started"
        finally:
            stream.close()


def test_the_stream_ending_before_the_event_fails_with_what_was_awaited() -> None:
    with event_server(recorded(2)) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match=r"/events ended before change\.resolved"):
                stream.wait(
                    lambda e: e.type == "change.resolved", 10, "change.resolved", after_seq=0
                )
        finally:
            stream.close()


def test_an_error_event_fails_the_wait_at_once() -> None:
    with event_server([*recorded(1), error_event(2)]) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match="an error event came before x: boom"):
                stream.wait(lambda e: e.type == "change.proposed", 10, "x", after_seq=0)
        finally:
            stream.close()


def test_an_event_that_is_not_the_contract_fails() -> None:
    with event_server(['{"seq": 1, "type": "nonsense"}']) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match="an event does not validate"):
                stream.wait(lambda e: True, 10, "anything", after_seq=0)
        finally:
            stream.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Linux flock lock proof")
def test_the_runtime_lock_is_free_unless_someone_holds_it(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Linux flock lock proof")
    descriptor = os.open(tmp_path / "server.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert demo_check.lock_is_free(tmp_path) is False
    finally:
        os.close(descriptor)  # closing the descriptor lets the lock go
    assert demo_check.lock_is_free(tmp_path) is True


def test_a_missing_lock_file_is_an_error_not_a_free_lock(tmp_path: Path) -> None:
    with pytest.raises(smoke.SmokeError, match="runtime lock cannot be opened"):
        demo_check.lock_is_free(tmp_path)


def test_the_makefile_backstop_is_above_the_computed_worst_case() -> None:
    makefile = (demo_check.REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "--print-deadline" in makefile  # one source of truth: demo_check computes it
    match = re.search(r"deadline \+ (\d+)", makefile)
    assert match is not None and 0 < int(match.group(1)) <= 300
    assert demo_check.OVERALL_DEADLINE > 540  # the old outer timeout killed healthy runs


def test_the_overall_deadline_is_the_worst_case_sum_plus_a_small_margin() -> None:
    per_run = (
        2 * demo_check.START_DEADLINE
        + 2 * demo_check.START_AGENT_DEADLINE
        + 3 * demo_check.PROPOSE_DEADLINE
        + 40 * demo_check.DECIDE_DEADLINE
        + 2 * demo_check.STOP_DEADLINE
        + demo_check.REQUESTS_PER_RUN * demo_check.REQUEST_TIMEOUT
    )
    assert demo_check.RUNS * per_run + demo_check.OVERALL_MARGIN == demo_check.OVERALL_DEADLINE
    assert 0 < demo_check.OVERALL_MARGIN <= 300


def test_an_expired_overall_deadline_names_the_phase() -> None:
    clock = demo_check.OverallClock(started=0.0, deadline_s=10.0)
    clock.phase = "run 2: POST /agents (venv build)"
    with pytest.raises(smoke.SmokeError, match=r"overall deadline .* during run 2: POST /agents"):
        clock.check(now=11.0)


def test_an_expiry_before_the_stop_is_named_for_the_phase_it_expired_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = subprocess.Popen(
        [sys.executable, "-c", SLEEPER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: server)
    monkeypatch.setattr(smoke, "wait_for_health", lambda *args, **kwargs: None)

    def decide(
        base: str, first: Any, clock: demo_check.OverallClock, run: int, runtime_dir: Path
    ) -> Any:
        clock.phase = "run 1: wait for graph.delta"
        clock.started -= clock.deadline_s + 1
        return object()

    monkeypatch.setattr(demo_check, "serve_and_decide", decide)
    try:
        with pytest.raises(
            smoke.SmokeError, match=r"overall deadline .* during run 1: wait for graph\.delta"
        ):
            demo_check.run_once(
                1, tmp_path, tmp_path / ".runtime", None, demo_check.OverallClock(deadline_s=30)
            )
        for _ in range(100):
            if server.poll() is not None:
                break
            time.sleep(0.02)
        assert server.poll() is not None  # the stop still ran, bounded, after the expiry
    finally:
        server.kill()
        server.wait(timeout=10)


def test_the_server_gets_no_provider_key_even_one_set_in_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_API_KEY", "test-key")
    env = demo_check.server_env(8000, tmp_path / ".runtime")
    assert env["META_API_KEY"] == "" and env["BASETEN_API_KEY"] == ""
    assert env["OVERSIGHT_AGENT_MODEL"] == env["OVERSIGHT_AGENT_MODEL_B"] == "fake"
    assert env["OVERSIGHT_FAKE_SCRIPT"] == phases.SCRIPTS["A"]
    assert env["OVERSIGHT_FAKE_SCRIPT_B"] == phases.SCRIPTS["B"]
    assert float(env["OVERSIGHT_BUDGET_USD"]) == phases.BUDGET_USD
    assert env["OVERSIGHT_CROSS_REVIEW"] == "false"
    assert env["OVERSIGHT_WORKSPACE_SOURCE"] == ""


def sample(kind: str, seq: int, **payload: Any) -> dict[str, Any]:
    event = next(
        json.loads(line)
        for line in LOG.read_text().splitlines()
        if json.loads(line)["type"] == kind
    )
    event["seq"] = seq
    event["payload"].update(payload)
    return event


def test_wait_correlates_agent_change_revision_and_exclusive_seq() -> None:
    events = [
        sample("change.proposed", 1),
        sample("change.proposed", 2, agent_id="agent-2", change_id="change-2", revision=2),
        sample("change.proposed", 3, revision=1),
        sample("change.proposed", 4, change_id="change-2", revision=2),
        sample("change.proposed", 5, revision=2),
    ]
    with event_server([json.dumps(e) for e in events]) as base:
        stream = demo_check.EventStream(base)
        try:
            found = stream.wait(
                lambda e: e.type == "change.proposed",
                1,
                "A revised proposal",
                after_seq=1,
                agent_id="agent-1",
                change_id="change-1",
                revision=2,
            )
            assert found.seq == 5
            # An event already read can satisfy the same command epoch, but never a new one.
            assert (
                stream.wait(
                    lambda e: True,
                    1,
                    "same epoch",
                    after_seq=4,
                    agent_id="agent-1",
                    change_id="change-1",
                    revision=2,
                ).seq
                == 5
            )
            with pytest.raises(smoke.SmokeError, match="ended before A next proposal"):
                stream.wait(
                    lambda e: True,
                    1,
                    "A next proposal",
                    after_seq=5,
                    agent_id="agent-1",
                    change_id="change-1",
                    revision=2,
                )
        finally:
            stream.close()


def test_wait_rejects_a_missing_seq_instead_of_certifying_partial_history() -> None:
    with event_server([json.dumps(sample("change.proposed", 2))]) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match="seq 2, expected 1"):
                stream.wait(lambda e: True, 1, "anything", after_seq=0)
        finally:
            stream.close()


@pytest.mark.parametrize(
    ("kind", "revision", "state", "message"),
    [
        ("agent.step", None, None, "B agent.step"),
        ("change.proposed", 1, None, "B change.proposed revision 1"),
        ("change.proposed", 2, None, "B change.proposed revision 2"),
        ("agent.status", None, "revising", "B agent.status revising"),
        ("agent.status", None, "stopped", "B agent.status stopped"),
        ("change.resolved", 1, None, "B change.resolved revision 1"),
        ("usage.tick", None, None, "B usage.tick"),
    ],
)
def test_missing_phase_event_names_the_agent_state_and_revision(
    kind: str,
    revision: int | None,
    state: str | None,
    message: str,
) -> None:
    with event_server(recorded(1)) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match=re.escape("ended before " + message)):
                phases.wait_for(stream, B, kind, 1, revision=revision, state=state)
        finally:
            stream.close()


@pytest.mark.parametrize("sequences", [(1, 8, 7, 10), (8, 1, 10, 7)])
def test_overlap_failure_identifies_the_missing_cross_agent_order(
    sequences: tuple[int, ...],
) -> None:
    with pytest.raises(smoke.SmokeError, match="A/B did not overlap"):
        phases.check_overlap(*(SimpleNamespace(seq=seq) for seq in sequences))


@pytest.mark.parametrize(
    "bad",
    [
        {"brake": "blocked"},
        {"budget_usd": 0},
        {"cost_usd": 0},
        {"spent_usd": 1},
    ],
)
def test_usage_failure_names_the_slot_and_brake_expectations(bad: dict[str, Any]) -> None:
    payload = {"brake": "ok", "budget_usd": 150, "cost_usd": None, "spent_usd": 0} | bad
    with pytest.raises(smoke.SmokeError, match=r"B usage\.tick: expected brake ok"):
        phases.check_usage(SimpleNamespace(payload=SimpleNamespace(**payload)), "B")


def stub_api(monkeypatch: pytest.MonkeyPatch, seq: int = 0) -> None:
    monkeypatch.setattr(
        phases,
        "graph_of",
        lambda *a: SimpleNamespace(seq=seq, graph=SimpleNamespace(version="before", nodes=[])),
    )
    monkeypatch.setattr(phases, "request", lambda *a, **kw: demo_check.Reply(202, {}, b""))


def test_request_changes_failure_identifies_a_lost_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_api(monkeypatch)
    resolution = sample(
        "change.resolved",
        1,
        decision="changes_requested",
        comment="wrong",
        merge=None,
        merge_commit=None,
    )
    with event_server([json.dumps(resolution)]) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(smoke.SmokeError, match="A request changes did not preserve"):
                phases.request_revision(
                    base,
                    stream,
                    demo_check.OverallClock(),
                    A,
                    SimpleNamespace(payload=SimpleNamespace(revision=1, head_commit="head-1")),
                )
        finally:
            stream.close()


def test_approve_failure_cannot_reuse_the_request_changes_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_api(monkeypatch, seq=1)
    resolution = sample(
        "change.resolved",
        1,
        decision="changes_requested",
        comment="Shorter",
        merge=None,
        merge_commit=None,
        revision=2,
    )
    with event_server([json.dumps(resolution)]) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(
                smoke.SmokeError, match=r"ended before A change\.resolved revision 2"
            ):
                phases.approve_a(
                    base,
                    stream,
                    demo_check.OverallClock(),
                    A,
                    SimpleNamespace(payload=SimpleNamespace(revision=2, head_commit="head-2")),
                    cast(GraphResponse, None),
                )
        finally:
            stream.close()


def test_file_view_delta_without_the_added_function_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_api(monkeypatch)
    file = {"path": "shop/pricing.py", "hash": "abc", "content": "", "agent_id": None}
    puts: list[dict[str, Any]] = []

    def reply(*a: Any, **kw: Any) -> demo_check.Reply:
        if "put" in kw:
            puts.append(kw["put"])
        return demo_check.Reply(200, {}, json.dumps(file).encode())

    monkeypatch.setattr(phases, "request", reply)
    delta = sample("graph.delta", 1, cause="user_edit", from_version="before", nodes_added=[])
    with event_server([json.dumps(delta)]) as base:
        stream = demo_check.EventStream(base)
        try:
            with pytest.raises(
                smoke.SmokeError, match=r"file view graph\.delta is missing the added"
            ):
                phases.edit_main(base, stream, demo_check.OverallClock())
            assert len(puts) == 1 and "agent_id" not in puts[0]
        finally:
            stream.close()


def test_stop_requires_rejected_stopped_and_checks_worktree_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_api(monkeypatch, seq=2)
    monkeypatch.setattr(phases, "request_revision", lambda *a: SimpleNamespace(seq=0))
    monkeypatch.setattr(phases, "DECIDE_DEADLINE", 0.01)
    tree = tmp_path / "worktrees" / B.agent_id
    tree.mkdir(parents=True)
    proposal = SimpleNamespace(payload=SimpleNamespace(revision=1, head_commit="head-1"))
    for reason, message in [
        (None, "B Stop did not resolve"),
        ("stopped", "B worktree still exists"),
    ]:
        events = [
            sample("change.updated", 1, agent_id=B.agent_id, change_id=B.change_id),
            sample("agent.step", 2, agent_id=B.agent_id, tool="edit_file", result="ok"),
            sample(
                "agent.status",
                3,
                agent_id=B.agent_id,
                change_id=B.change_id,
                slot="B",
                state="stopped",
            ),
            sample(
                "change.resolved",
                4,
                agent_id=B.agent_id,
                change_id=B.change_id,
                decision="rejected",
                reason=reason,
                merge=None,
                merge_commit=None,
            ),
        ]
        with event_server([json.dumps(e) for e in events]) as base:
            stream = demo_check.EventStream(base)
            try:
                with pytest.raises(smoke.SmokeError, match=message):
                    phases.stop_working_b(
                        base, stream, demo_check.OverallClock(), B, proposal, tmp_path
                    )
            finally:
                stream.close()


def test_pending_overlay_survives_request_changes_and_is_removed_on_stop() -> None:
    events = [SimpleNamespace(**sample("change.proposed", 1, agent_id=B.agent_id))]
    events[0].payload = SimpleNamespace(**events[0].payload)
    events[0].payload.graph = SimpleNamespace(
        nodes_added=[], nodes_modified=["node"], nodes_removed=[]
    )
    assert phases.pending_nodes(events, B.agent_id) == {"node"}
    for decision in ("changes_requested", "rejected"):
        events.append(
            SimpleNamespace(
                type="change.resolved",
                payload=SimpleNamespace(agent_id=B.agent_id, decision=decision, merge=None),
            )
        )
        assert phases.pending_nodes(events, B.agent_id) == (
            {"node"} if decision == "changes_requested" else set()
        )


@pytest.mark.parametrize("verb", ["request", "approve"])
def test_demo_decisions_send_the_observed_guard(monkeypatch: pytest.MonkeyPatch, verb: str) -> None:
    stub_api(monkeypatch)
    captured = []

    def capture(*args, **kwargs):
        captured.append(kwargs["post"])
        raise ValueError("captured request")

    monkeypatch.setattr(phases, "request", capture)
    proposal = SimpleNamespace(payload=SimpleNamespace(revision=7, head_commit="observed-head"))
    with pytest.raises(ValueError, match="captured request"):
        if verb == "request":
            phases.request_revision(
                "http://localhost",
                cast(demo_check.EventStream, None),
                demo_check.OverallClock(),
                A,
                proposal,
            )
        else:
            phases.approve_a(
                "http://localhost",
                cast(demo_check.EventStream, None),
                demo_check.OverallClock(),
                A,
                proposal,
                cast(GraphResponse, None),
            )
    expected = {"revision": 7, "head_commit": "observed-head"}
    if verb == "request":
        expected["comment"] = "Shorten the docstring."
    assert captured == [expected]
