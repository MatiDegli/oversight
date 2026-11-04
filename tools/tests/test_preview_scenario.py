"""Fake transports prove consumer ordering, never C or browser acceptance.

Test-only PreviewDouble envelopes deliberately are not proposed public API types.
A/B's absent public preview contract remains an explicit integration dependency.
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

import preview_scenario as story
from oversight.agents import ScriptedModel, parse_script
from oversight_contracts import CONTRACT_VERSION

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]
BASE = "b" * 40
ORIGIN = "http://localhost:8710"


class Stream(httpx.AsyncByteStream):
    def __init__(self, queue):
        self.queue = queue

    async def __aiter__(self):
        while True:
            yield await self.queue.get()


class Backend:
    def __init__(self, *, fake=True, stale=False, fail=None):
        self.queue = asyncio.Queue()
        self.log = []
        self.fake, self.stale, self.fail = fake, stale, fail
        self.revision = 0
        self.owned = False
        self.nonce = "a" * 64
        self.seq = 0
        self.push(
            "run.started",
            dict(
                run_id="run-20261103-000000",
                contract_version=CONTRACT_VERSION,
                prompt_version="test",
                workspace="demo-repo",
                base_commit=BASE,
                models=[
                    dict(
                        id="fake",
                        label="Fake",
                        provider="fake" if fake else "meta",
                        model="scripted",
                        reasoning=None,
                    )
                ],
                slots=[dict(slot="A", default_model="fake")],
                limits=dict(
                    max_steps=30,
                    max_output_tokens=4096,
                    max_wall_clock_s=60,
                    test_timeout_s=10,
                    cone_max_depth=3,
                    cone_max_nodes=40,
                ),
                budget=dict(budget_usd=1, spent_usd=0, warn_at=[0.5], block_at=0.9),
                merge_mode=dict(mode="ask_me", revision=0),
            ),
        )

    def push(self, kind, payload):
        self.seq += 1
        self.queue.put_nowait(
            (
                "data: "
                + json.dumps(
                    dict(seq=self.seq, ts="2026-11-03T00:00:00.000Z", type=kind, payload=payload)
                )
                + "\n\n"
            ).encode()
        )

    def proposal(self):
        self.revision += 1
        self.push(
            "change.proposed",
            dict(
                change_id="change-1",
                agent_id="agent-1",
                revision=self.revision,
                task="Preview",
                summary="Fake",
                base_commit=BASE,
                head_commit=str(self.revision) * 40,
                files=[],
                lines_added=1,
                lines_removed=0,
                graph=dict(
                    nodes_added=[],
                    nodes_modified=[],
                    nodes_removed=[],
                    edges_added=[],
                    edges_removed=[],
                ),
                tests=dict(
                    outcome="passed", passed=29, failed=0, skipped=0, duration_ms=1, failed_tests=[]
                ),
                impact=dict(
                    seeds=[],
                    entries=[],
                    edges_followed="exact",
                    max_depth=3,
                    max_nodes=40,
                    omitted_by_depth=0,
                    omitted_by_limit=0,
                    tests_reaching=0,
                ),
            ),
        )

    async def handle(self, request):
        path = request.url.path
        self.log.append((request.method, path))
        if path == "/events":
            return httpx.Response(200, stream=Stream(self.queue))
        if path == "/files":
            assert not self.owned
            content = (ROOT / "demo-repo/shop/pricing.py").read_text()
            agent = request.url.params.get("agent")
            if agent and self.revision == 2:
                content = content.replace('"SPRING10": 10}', '"SPRING10": 10, "DEMO10": 10}')
            return httpx.Response(200, json=dict(agent_id=agent, content=content))
        if path == "/health":
            return httpx.Response(200, json=dict(mode="live", run_id="run-20261103-000000"))
        if path == "/merge-mode":
            return httpx.Response(200, json=dict(mode="ask_me", revision=0))
        if path == "/graph":
            return httpx.Response(
                200, json=dict(run_id="run-20261103-000000", graph=dict(commit=BASE))
            )
        if path == "/agents":
            self.proposal()
            return httpx.Response(201, json=dict(agent_id="agent-1", change_id="change-1"))
        if path.endswith("request-changes"):
            assert not self.owned, "revision before verified stop"
            assert json.loads(request.content)["revision"] == 1
            self.proposal()
            return httpx.Response(202)
        if path.endswith("reject") or path == "/agents/agent-1/stop":
            assert not self.owned, "Git cleanup before verified stop"
            payload = dict(
                agent_id="agent-1",
                change_id="change-1",
                revision=self.revision,
                decision="rejected",
                reason=None,
                comment=None,
                merge=None,
                merge_commit=None,
                conflict=None,
                head_commit=str(self.revision) * 40,
            )
            self.push("change.resolved", payload)
            return httpx.Response(202)
        raise AssertionError("Unexpected backend request")

    async def shop(self, request):
        assert self.owned
        self.log.append((request.method, "shop"))
        if self.fail == "shop":
            raise httpx.ReadTimeout("private URL/token must not escape")
        if self.fail == "slow":
            await asyncio.sleep(10)
        total = 1500
        if request.method == "POST":
            from urllib.parse import parse_qs

            data = parse_qs(request.content.decode())
            assert data == dict(
                quantity_1=["1"], quantity_2=["1"], coupon=["DEMO10"], nonce=[self.nonce]
            )
            assert request.headers["origin"] == "null" and "cookie" not in request.headers
            total = 1350 if self.revision == 2 else 1500
            self.nonce = "c" * 64
        text = (
            f'<form action="/cart" method="post"><input type="hidden" name="nonce" '
            f'value="{self.nonce}"></form><output data-total-cents="{total}"></output>'
        )
        return httpx.Response(
            200,
            text=text,
            headers={
                "Content-Security-Policy": "default-src 'none'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors " + ORIGIN + "; sandbox allow-forms",
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )


class PreviewDouble:
    """Behavioral port double, not evidence for any future preview wire contract."""

    def __init__(self, backend):
        self.backend = backend
        self.target = None

    async def open(self, candidate):
        target = candidate
        self.target = target
        self.backend.log.append(("POST", "/previews"))
        assert target.opening() == dict(
            run_id="run-20261103-000000",
            agent_id="agent-1",
            revision=self.backend.revision,
            head_commit=str(self.backend.revision) * 40,
        )
        self.backend.owned = True
        self.backend.nonce = "a" * 64
        if self.backend.fail == "open":
            raise story.ScenarioError("Opening response unavailable")

    async def ready(self, candidate):
        target = candidate
        self.backend.log.append(("GET", "ready-url"))
        story.require(target == self.target and not self.backend.stale, "Stale ready response")
        return "http://127.0.0.1:19999/"

    async def renew(self):
        self.backend.log.append(("POST", "/previews/test/lease"))

    async def stop(self):
        self.backend.log.append(("POST", "/previews/test/stop"))
        if self.backend.fail == "stop":
            raise story.ScenarioError("Verified termination unavailable")
        self.backend.owned = False
        self.backend.log.append(("verified", "terminated"))


async def run_backend(backend):
    async with (
        httpx.AsyncClient(base_url=ORIGIN, transport=httpx.MockTransport(backend.handle)) as api,
        httpx.AsyncClient(transport=httpx.MockTransport(backend.shop)) as shop,
        story.Events(api) as events,
    ):
        return await story.Scenario(api, PreviewDouble(backend), shop, ORIGIN).run(events)


async def test_two_totals_stop_before_revision_and_final_rejection():
    backend = Backend()
    assert await run_backend(backend) == (1500, 1350)
    log = backend.log
    stops = [i for i, entry in enumerate(log) if entry == ("verified", "terminated")]
    assert len(stops) == 2
    assert stops[0] < log.index(("POST", "/changes/change-1/request-changes")) < stops[1]
    assert stops[1] < log.index(("POST", "/changes/change-1/reject"))
    assert log.count(("POST", "/previews")) == log.count(("GET", "ready-url")) == 2
    assert log.count(("POST", "shop")) == 2
    assert not backend.owned and not any("approve" in path for _, path in log)


@pytest.mark.parametrize(
    "ancestors,expected_origins,accepted",
    [
        (
            ORIGIN + " http://localhost:5710 http://127.0.0.1:5710",
            (ORIGIN, "http://localhost:5710", "http://127.0.0.1:5710"),
            True,
        ),
        ("http://localhost:5710 " + ORIGIN, ("http://localhost:5710", ORIGIN), True),
        ("http://localhost:5710", None, False),
        (ORIGIN + " *", None, False),
        (ORIGIN + " http://*.example.com", None, False),
        (ORIGIN + "; script-src 'unsafe-inline'", None, False),
        (ORIGIN, None, True),
        (ORIGIN + " http://localhost:5710", None, False),
        ("http://127.0.0.1:8710", None, False),
        (ORIGIN + " " + ORIGIN, None, False),
        (ORIGIN, (ORIGIN, "http://localhost:5710"), False),
    ],
)
async def test_document_requires_exact_declared_csp_origins(ancestors, expected_origins, accepted):
    response = httpx.Response(
        200,
        text='<form action="/cart" method="post"><input type="hidden" name="nonce" '
        f'value="{"a" * 64}"></form><output data-total-cents="1500"></output>',
        headers={
            "Content-Security-Policy": "default-src 'none'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors " + ancestors + "; sandbox allow-forms",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )
    if accepted:
        assert story.document(response, ORIGIN, expected_origins=expected_origins).total == 1500
    else:
        with pytest.raises(story.ScenarioError, match="CSP"):
            story.document(response, ORIGIN, expected_origins=expected_origins)


async def test_direct_document_uses_explicit_numeric_multiple_origin_configuration():
    backend = Backend()
    backend.owned = True
    response = await backend.shop(httpx.Request("GET", "http://127.0.0.1:19999/"))
    configured = ("http://127.0.0.1:8710", "http://127.0.0.1:5710")
    response.headers["content-security-policy"] = response.headers[
        "content-security-policy"
    ].replace(ORIGIN, " ".join(configured))
    assert story.document(response, configured[0], expected_origins=configured).total == 1500
    with pytest.raises(story.ScenarioError, match="CSP"):
        story.document(response, configured[0])


@pytest.mark.parametrize(
    "sandbox",
    [
        "",
        "; sandbox",
        "; sandbox allow-scripts",
        "; sandbox allow-forms allow-scripts",
        "; sandbox allow-forms allow-same-origin",
        "; sandbox allow-forms allow-popups",
    ],
)
async def test_document_refuses_missing_or_widened_sandbox(sandbox):
    backend = Backend()
    backend.owned = True
    response = await backend.shop(httpx.Request("GET", "http://127.0.0.1:19999/"))
    response.headers["content-security-policy"] = response.headers[
        "content-security-policy"
    ].replace("; sandbox allow-forms", sandbox)
    with pytest.raises(story.ScenarioError, match="CSP"):
        story.document(response, ORIGIN)


@pytest.mark.parametrize(
    "directive",
    [
        "script-src 'unsafe-inline'",
        "connect-src *",
        "default-src *",
        "sandbox allow-forms",
        "frame-ancestors " + ORIGIN,
    ],
)
async def test_document_refuses_extra_or_duplicate_directives(directive):
    backend = Backend()
    backend.owned = True
    response = await backend.shop(httpx.Request("GET", "http://127.0.0.1:19999/"))
    response.headers["content-security-policy"] += "; " + directive
    with pytest.raises(story.ScenarioError, match="CSP"):
        story.document(response, ORIGIN)


@pytest.mark.parametrize(
    "header,value",
    [
        ("referrer-policy", None),
        ("referrer-policy", "unsafe-url"),
        ("cache-control", None),
        ("cache-control", "public"),
        ("set-cookie", "session=untrusted"),
        ("location", "/elsewhere"),
        ("access-control-allow-origin", "*"),
        ("access-control-allow-credentials", "true"),
    ],
)
async def test_document_preserves_independent_response_security_checks(header, value):
    backend = Backend()
    backend.owned = True
    response = await backend.shop(httpx.Request("GET", "http://127.0.0.1:19999/"))
    if value is None:
        del response.headers[header]
    else:
        response.headers[header] = value
    with pytest.raises(story.ScenarioError):
        story.document(response, ORIGIN)


@pytest.mark.parametrize(
    "old,new",
    [
        ('action="/cart"', 'action="https://external.invalid/cart"'),
        ('method="post"', 'method="get"'),
        ('type="hidden"', 'type="text"'),
        ("a" * 64, "invalid-nonce"),
        ("</form>", '</form><form action="/cart" method="post"></form>'),
        ("</form>", '<input type="hidden" name="nonce" value="' + "a" * 64 + '"></form>'),
        ("<form", '<form onsubmit="unsafe()"'),
        ("</form>", '</form><script src="/active.js"></script>'),
        ("</form>", "</form><style>body{display:none}</style>"),
        ("</form>", '</form><iframe src="/active"></iframe>'),
    ],
)
async def test_document_preserves_form_nonce_and_inactive_content_checks(old, new):
    backend = Backend()
    backend.owned = True
    original = await backend.shop(httpx.Request("GET", "http://127.0.0.1:19999/"))
    response = httpx.Response(200, headers=original.headers, text=original.text.replace(old, new))
    with pytest.raises(story.ScenarioError):
        story.document(response, ORIGIN)


@pytest.mark.parametrize("failure", ["shop", "open"])
async def test_failure_closes_preview_before_discard(failure):
    backend = Backend(fail=failure)
    with pytest.raises((story.ScenarioError, httpx.ReadTimeout)):
        await run_backend(backend)
    assert not backend.owned
    assert backend.log.index(("verified", "terminated")) < backend.log.index(
        ("POST", "/agents/agent-1/stop")
    )
    assert not any("request-changes" in path for _, path in backend.log)


async def test_stale_ready_response_never_opens_document():
    backend = Backend(stale=True)
    with pytest.raises(story.ScenarioError, match="Stale"):
        await run_backend(backend)
    assert not backend.owned
    assert not any(path == "shop" for _, path in backend.log)


async def test_uncertain_stop_forbids_all_later_git_commands():
    backend = Backend(fail="stop")
    with pytest.raises(story.ScenarioError, match="termination"):
        await run_backend(backend)
    assert backend.owned
    assert not any("reject" in path or "request-changes" in path for _, path in backend.log)


async def test_non_fake_refusal_before_task():
    backend = Backend(fake=False)
    with pytest.raises(story.ScenarioError, match="non-fake"):
        await run_backend(backend)
    assert ("POST", "/agents") not in backend.log


async def test_scenario_deadline_still_closes_and_rejects(monkeypatch):
    monkeypatch.setattr(story, "SCENARIO_SECONDS", 0.05)
    backend = Backend(fail="slow")
    with pytest.raises(TimeoutError):
        await run_backend(backend)
    assert not backend.owned and ("POST", "/agents/agent-1/stop") in backend.log


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8710",
        "http://127.0.0.1:8710",
        "http://127.0.0.1",
        "https://127.0.0.1:8710",
        "http://127.0.0.1:8710/?token=private",
        "http://user:pass@127.0.0.1:8710",
        "http://127.0.0.1:99999",
    ],
)
async def test_explicit_loopback_only(url):
    with pytest.raises(story.ScenarioError):
        story.loopback_url(url, root=True)


async def test_localhost_backend_and_exact_opposite_frame_are_supported():
    assert story.loopback_url(ORIGIN + "/", root=True) == ORIGIN
    assert story.loopback_url("http://127.0.0.1:19999/") == "http://127.0.0.1:19999/"


@pytest.mark.parametrize(
    "root,urls",
    [
        (
            True,
            [
                "http://localhost",
                "https://localhost:8710",
                "http://localhost:99999",
                "http://user:pass@localhost:8710",
                "http://localhost:8710/?token=private",
                "http://localhost:8710/#private",
                "http://localhost:8710/graph",
            ],
        ),
        (
            False,
            [
                "http://localhost:19999/",
                "http://127.0.0.1",
                "https://127.0.0.1:19999/",
                "http://127.0.0.1:99999/",
                "http://user:pass@127.0.0.1:19999/",
                "http://127.0.0.1:19999/?token=private",
                "http://127.0.0.1:19999/#private",
                "http://127.0.0.1:19999/cart",
                "http://example.com:19999/",
            ],
        ),
    ],
)
async def test_parent_and_frame_keep_unsafe_url_refusals(root, urls):
    for url in urls:
        with pytest.raises(story.ScenarioError):
            story.loopback_url(url, root=root)


@pytest.mark.parametrize("url", ["http://localhost:19999/", "http://127.0.0.1:8710/"])
async def test_wrong_frame_or_shared_port_never_opens_document(monkeypatch, url):
    async def ready(self, candidate):
        return url

    monkeypatch.setattr(PreviewDouble, "ready", ready)
    backend = Backend()
    with pytest.raises(story.ScenarioError):
        await run_backend(backend)
    assert not backend.owned
    assert not any(path == "shop" for _, path in backend.log)
    assert ("POST", "/agents/agent-1/stop") in backend.log


class Exit(asyncio.SubprocessProtocol):
    def __init__(self):
        self.done = asyncio.get_running_loop().create_future()
        self.output = bytearray()

    def pipe_data_received(self, fd, data):
        self.output.extend(data[: max(0, 8192 - len(self.output))])

    def connection_lost(self, exc):
        if not self.done.done():
            self.done.set_result(None)


async def suite(tree):
    loop = asyncio.get_running_loop()
    transport, protocol = await asyncio.wait_for(
        loop.subprocess_exec(
            Exit,
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            cwd=tree,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tree), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        ),
        5,
    )
    try:
        await asyncio.wait_for(asyncio.shield(protocol.done), 20)
        assert transport.get_returncode() == 0, bytes(protocol.output).decode()
    finally:
        if transport.get_returncode() is None:
            transport.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(protocol.done), 2)
            except TimeoutError:
                transport.kill()
                await asyncio.wait_for(asyncio.shield(protocol.done), 2)
        transport.close()


async def test_script_public_fake_interface_two_legal_proposals(tmp_path):
    tree = tmp_path / "candidate"
    shutil.copytree(ROOT / "demo-repo", tree)
    original = (tree / "shop/pricing.py").read_bytes()
    model = ScriptedModel(parse_script(story.SCRIPT.read_text()))
    read = set()
    finishes = []
    tests = 0
    while reply := await model.complete([], []):
        if not reply.tool_calls:
            break
        for call in reply.tool_calls:
            args = json.loads(call.arguments)
            if call.name == "read_file":
                read.add(args["path"])
            elif call.name == "edit_file":
                assert args["path"] in read
                path = tree / args["path"]
                text = path.read_text()
                assert text.count(args["old_text"]) == 1
                path.write_text(text.replace(args["old_text"], args["new_text"]))
            elif call.name == "run_tests":
                await suite(tree)
                tests += 1
            elif call.name == "finish":
                assert tests == len(finishes) + 1
                finishes.append(args)
                if len(finishes) == 1:
                    assert (tree / "shop/pricing.py").read_bytes() == original
                    assert (
                        'apply_coupon(1500, "DEMO10") == 1500'
                        in (tree / "tests/test_pricing.py").read_text()
                    )
                read.clear()  # Revision 2 must read current files before editing.
            else:
                pytest.fail("New script added an unexpected capability")
    assert len(finishes) == tests == 2
    assert '"DEMO10": 10' in (tree / "shop/pricing.py").read_text()
    assert 'apply_coupon(1500, "DEMO10") == 1350' in (tree / "tests/test_pricing.py").read_text()
    assert (ROOT / "demo-repo/shop/pricing.py").read_bytes() == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("change_id", "change-2"),
        ("revision", 2),
        ("base_commit", "c" * 40),
        ("head_commit", BASE),
    ],
)
async def test_proposal_identity_revision_and_base_fencing(field, value):
    backend = Backend()
    backend.queue.get_nowait()
    backend.proposal()
    raw = json.loads(backend.queue.get_nowait().decode().strip()[6:])
    raw["payload"][field] = value
    event = story.EVENT.validate_python(raw)
    async with httpx.AsyncClient(base_url=ORIGIN) as client:
        events = story.Events(client)
        events.seen = [event]
        scenario = story.Scenario(client, PreviewDouble(backend), client, ORIGIN)
        scenario.agent_id, scenario.change_id = "agent-1", "change-1"
        scenario.run_id, scenario.main = "run-20261103-000000", BASE
        with pytest.raises(story.ScenarioError, match="provenance"):
            await scenario.proposal(events, 1, 0)


async def test_event_wait_has_deadline_and_rejects_sequence_gaps(monkeypatch):
    monkeypatch.setattr(story, "WAIT_SECONDS", 0.02)
    async with httpx.AsyncClient(base_url=ORIGIN) as client:
        events = story.Events(client)
        with pytest.raises(TimeoutError):
            await events.wait("change.proposed")
        backend = Backend()
        raw = json.loads(backend.queue.get_nowait().decode().strip()[6:])
        raw["seq"] = 2
        await events.queue.put(story.EVENT.validate_python(raw))
        with pytest.raises(story.ScenarioError, match="sequence"):
            await events.wait("run.started")
