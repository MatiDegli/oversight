"""The sandboxed document's admission never widens the unchanged Oversight API guard."""

from unittest.mock import AsyncMock

import pytest
from preview_adapter_helpers import launched

from oversight.api import ApiGuard, AuthorityPolicy

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "origin", [None, "http://localhost:8710", "http://127.0.0.1:19999", "null", "null, null"]
)
async def test_post_requires_exact_null_origin_even_with_nonce(tmp_path, origin):
    import re
    from urllib.parse import urlencode

    async with launched(tmp_path) as child:
        response = await child.request()
        match = re.search(rb'name="nonce" value="([a-f0-9]{64})"', response.body)
        assert match is not None
        nonce = match[1].decode()
        body = urlencode(dict(quantity_1=1, quantity_2=1, coupon="", nonce=nonce)).encode()
        headers = [
            ("Host", f"127.0.0.1:{child.port}"),
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Content-Length", str(len(body))),
        ]
        if origin is not None:
            headers.append(("Origin", origin))
        reply = await child.request("POST", "/cart", body, headers)
        assert reply.status == (200 if origin == "null" else 403)


@pytest.mark.parametrize("path", ["/", "/_oversight_ready", "/unknown"])
@pytest.mark.parametrize("origin", ["null", "http://127.0.0.1:19999", "https://attacker.invalid"])
async def test_other_routes_refuse_null_and_foreign_origins(tmp_path, path, origin):
    async with launched(tmp_path) as child:
        reply = await child.request(
            path=path,
            headers=[
                ("Host", f"127.0.0.1:{child.port}"),
                ("Origin", origin),
            ],
        )
        assert reply.status == 403


@pytest.mark.parametrize(
    "kind", ["missing", "foreign", "localhost", "duplicate_host", "duplicate_origin"]
)
async def test_exact_host_and_single_origin(tmp_path, kind):
    async with launched(tmp_path) as child:
        host = f"127.0.0.1:{child.port}"
        headers = [] if kind == "missing" else [("Host", host)]
        if kind == "foreign":
            headers = [("Host", "attacker.invalid")]
        elif kind == "localhost":
            headers = [("Host", f"localhost:{child.port}")]
        elif kind == "duplicate_host":
            headers.append(("Host", host))
        elif kind == "duplicate_origin":
            headers += [("Origin", "http://localhost:8710")] * 2
        assert (await child.request(headers=headers)).status == 403


@pytest.mark.parametrize("path", ["/previews", "/agents", "/events", "/"])
@pytest.mark.parametrize("origin", [b"null", b"http://127.0.0.1:19999", None])
async def test_api_guard_refuses_preview_and_null_preserves_cli(path, origin):
    downstream = AsyncMock()
    sent = []

    async def send(message):
        sent.append(message)

    headers = [(b"host", b"localhost:8710")]
    if origin is not None:
        headers.append((b"origin", origin))
    await ApiGuard(downstream, policy=AuthorityPolicy(8710, 5710))(
        {"type": "http", "method": "POST", "path": path, "headers": headers},
        AsyncMock(),
        send,
    )
    if origin is None:
        downstream.assert_awaited_once()
        assert not sent
    else:
        downstream.assert_not_called()
        assert sent[0]["status"] == 403
        assert not any(key.startswith(b"access-control") for key, _ in sent[0]["headers"])


@pytest.mark.parametrize("path,status", [("/", 200), ("/_oversight_ready", 200), ("/missing", 404)])
async def test_shop_csp_sandbox_on_all_responses(tmp_path, path, status):
    async with launched(tmp_path) as child:
        reply = await child.request(path=path)
        assert reply.status == status
        reply.policy()
        assert reply.headers["content-security-policy"].endswith("; sandbox allow-forms")
        refused = await child.request(path=path, headers=[("Host", "foreign.invalid")])
        assert refused.status == 403
        refused.policy()
        assert (
            refused.headers["content-security-policy"] == reply.headers["content-security-policy"]
        )
