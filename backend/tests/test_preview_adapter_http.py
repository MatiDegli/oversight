"""Actual socket GET/form POST arithmetic, framing, nonce and fixed error tests."""

import asyncio
import re
from urllib.parse import urlencode

import pytest
from preview_adapter_helpers import copy_shop, launched

pytestmark = pytest.mark.anyio


def nonce(reply):
    match = re.search(rb'name="nonce" value="([a-f0-9]{64})"', reply.body)
    assert match is not None
    return match[1].decode()


async def post(child, token, quantities=(1, 1), coupon="", extra=()):
    body = urlencode(
        dict(quantity_1=quantities[0], quantity_2=quantities[1], coupon=coupon, nonce=token)
    ).encode()
    return await child.request(
        "POST",
        "/cart",
        body,
        [
            ("Host", f"127.0.0.1:{child.port}"),
            ("Origin", "null"),
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Content-Length", str(len(body))),
            *extra,
        ],
    )


@pytest.mark.parametrize(
    ("quantities", "coupon", "total"),
    [
        ((1, 1), "DEMO10", 1500),
        ((0, 0), "", 0),
        ((0, 1), "", 500),
        ((1, 0), "", 1000),
        ((20, 60), "", 42000),
        ((99, 99), "", 118800),
        ((1, 1), " welcome5 ", 1425),
        ((1, 1), " SpRiNg10 ", 1350),
        ((20, 60), "SPRING10", 37800),
        ((1, 1), "unknown", 1500),
    ],
)
async def test_shop_arithmetic_through_inherited_socket(tmp_path, quantities, coupon, total):
    setup = "def refused(*a): raise AssertionError('binding forbidden')\n"
    setup += "socket.socket.bind=refused\nsocket.socket.listen=refused"
    async with launched(tmp_path, setup=setup) as child:
        initial = await child.request()
        assert b'data-total-cents="1500"' in initial.body
        response = await post(child, nonce(initial), quantities, coupon)
        assert response.status == 200
        assert f'data-total-cents="{total}"'.encode() in response.body
        assert nonce(initial) != nonce(response)
        assert '<form action="/cart" method="post">' in response.body.decode()
        for forbidden in (b"<script", b"<style", b"style=", b"src=", b"http://", b"https://"):
            assert forbidden not in response.body


async def test_nonce_readiness_rotation_replay_and_cookie_independence(tmp_path):
    async with launched(tmp_path) as child:
        first = await child.request()
        await child.request(path="/_oversight_ready")
        accepted = await post(child, nonce(first), coupon="DEMO10", extra=(("Cookie", "anything"),))
        assert accepted.status == 200
        assert (await post(child, nonce(first))).status == 403
        second = await child.request()
        assert (await post(child, nonce(accepted))).status == 403
        assert (await post(child, nonce(second))).status == 200


async def test_dynamic_text_is_escaped(tmp_path):
    root = copy_shop(tmp_path)
    with (root / "shop/models.py").open("a") as stream:
        stream.write("\ndef format_cents(cents): return '<img src=x onerror=alert(1)>'\n")
    async with launched(tmp_path, root=root) as child:
        response = await post(child, nonce(await child.request()), coupon='"><script>bad</script>&')
        assert response.status == 200
        assert b"<script>" not in response.body and b"<img" not in response.body
        assert b"&lt;script&gt;" in response.body and b"&lt;img" in response.body
        assert b"&quot;" in response.body and b"&amp;" in response.body


@pytest.mark.parametrize("stage", ["subtotal", "coupon"])
@pytest.mark.parametrize("value", ["'<img src=x>'", "1500.5", "True"])
async def test_non_integer_candidate_totals_are_refused(tmp_path, stage, value):
    root = copy_shop(tmp_path)
    if stage == "subtotal":
        with (root / "shop/cart.py").open("a") as stream:
            stream.write(f"\nCart.total = lambda self: {value}\n")
    with (root / "shop/pricing.py").open("a") as stream:
        result = "1500" if stage == "subtotal" else value
        stream.write(f"\ndef apply_coupon(total, coupon): return {result}\n")
    with (root / "shop/models.py").open("a") as stream:
        stream.write("\ndef format_cents(total): return str(total)\n")
    async with launched(tmp_path, root=root) as child:
        reply = await child.request()
        assert reply.status == 500 and reply.body == b"Preview request refused.\n"


@pytest.mark.parametrize("stage", ["request_line", "headers", "body"])
async def test_dripping_reads_share_one_connection_deadline(tmp_path, stage):
    async with launched(tmp_path) as child:
        async with asyncio.timeout(6):
            reader, writer = await asyncio.open_connection("127.0.0.1", child.port)
            prefix = b"G"
            if stage != "request_line":
                prefix = f"POST /cart HTTP/1.1\r\nHost: 127.0.0.1:{child.port}\r\n".encode()
            if stage == "body":
                prefix += (
                    b"Origin: null\r\nContent-Type: application/x-www-form-urlencoded\r\n"
                    b"Content-Length: 4096\r\n\r\n"
                )

            async def drip():
                writer.write(prefix)
                await writer.drain()
                while True:
                    await asyncio.sleep(0.2)
                    writer.write(b"x")
                    await writer.drain()

            sender = asyncio.create_task(drip())
            try:
                response = await asyncio.wait_for(reader.read(), 3.5)
                assert b" 408 " in response and response.endswith(b"Preview request refused.\n")
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
                writer.close()
                await writer.wait_closed()
        assert (await child.request(path="/_oversight_ready")).status == 200


@pytest.mark.parametrize(
    "body",
    [
        b"quantity_1=1&quantity_2=1&coupon=x&nonce={nonce}&coupon=y",
        b"quantity_1=1&quantity_1=2&coupon=x&nonce={nonce}",
        b"quantity_1=1&quantity_2=1&coupon=x",
        b"quantity_1=100&quantity_2=1&coupon=&nonce={nonce}",
        b"quantity_1=-1&quantity_2=1&coupon=&nonce={nonce}",
        b"quantity_1=1.5&quantity_2=1&coupon=&nonce={nonce}",
        b"quantity_1=+1&quantity_2=1&coupon=&nonce={nonce}",
        b"quantity_1=1&quantity_2=1&coupon=%GG&nonce={nonce}",
        b"quantity_1=1&quantity_2=1&coupon=%FF&nonce={nonce}",
        b"quantity_1=1&quantity_2=1&coupon=" + b"x" * 65 + b"&nonce={nonce}",
        b"quantity_1=1&quantity_2=1&coupon=x&nonce=bad",
        b"x",
        b"\xff",
        b"x" * 4097,
    ],
)
async def test_bad_body_is_fixed_and_never_calls_shop(tmp_path, body):
    root = copy_shop(tmp_path)
    marker = tmp_path / "called"
    with (root / "shop/cart.py").open("a") as stream:
        stream.write(
            f"\ndef called(self):\n    open({str(marker)!r},'w').close()\n    return 0\n"
            "Cart.total=called\n"
        )
    async with launched(tmp_path, root=root) as child:
        body = body.replace(b"{nonce}", b"a" * 64)
        reply = await child.request(
            "POST",
            "/cart",
            body,
            [
                ("Host", f"127.0.0.1:{child.port}"),
                ("Origin", "null"),
                ("Content-Type", "application/x-www-form-urlencoded"),
                ("Content-Length", str(len(body))),
            ],
        )
        assert reply.status == (413 if len(body) > 4096 else 400)
        assert reply.body == b"Preview request refused.\n"
        assert not marker.exists()


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Content-Length", "1"), ("Content-Length", "1")],
        [("Content-Length", "+1")],
        [("Content-Length", "-1")],
        [("Transfer-Encoding", "chunked"), ("Content-Length", "1")],
        [("Content-Length", "1"), ("Content-Type", "application/x-www-form-urlencoded")],
        [("Content-Length", "1"), ("Expect", "100-continue")],
    ],
)
async def test_ambiguous_framing(tmp_path, headers):
    async with launched(tmp_path) as child:
        response = await child.request(
            "POST",
            "/cart",
            b"x",
            [
                ("Host", f"127.0.0.1:{child.port}"),
                ("Origin", "null"),
                ("Content-Type", "application/x-www-form-urlencoded"),
                *headers,
            ],
        )
        assert response.status == 400
        assert response.body == b"Preview request refused.\n"


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("GET", "/cart", 404),
        ("POST", "/", 404),
        ("GET", "/?q=x", 404),
        ("GET", "/shop/pricing.py", 404),
        ("GET", "/../", 404),
        ("POST", "/_oversight_ready", 404),
        ("PUT", "/", 405),
        ("HEAD", "/", 405),
        ("OPTIONS", "/", 405),
        ("TRACE", "/", 405),
        ("CONNECT", "/", 405),
        ("CUSTOM", "/", 405),
    ],
)
async def test_routes_and_methods(tmp_path, method, path, expected):
    async with launched(tmp_path) as child:
        reply = await child.request(method, path)
        assert reply.status == expected and reply.body == b"Preview request refused.\n"


async def test_slow_body_and_headers_have_two_second_timeout(tmp_path):
    async with launched(tmp_path) as child:
        reply = await child.request(
            "POST",
            "/cart",
            b"x",
            [
                ("Host", f"127.0.0.1:{child.port}"),
                ("Origin", "null"),
                ("Content-Type", "application/x-www-form-urlencoded"),
                ("Content-Length", "100"),
            ],
        )
        assert reply.status == 408
        async with asyncio.timeout(4):
            reader, writer = await asyncio.open_connection("127.0.0.1", child.port)
            try:
                writer.write(b"GET / HTTP/1.1\r\nHost:")
                await writer.drain()
                assert b"408" in await reader.read(10000)
            finally:
                writer.close()
                await writer.wait_closed()
        assert (await child.request()).status == 200


async def test_exception_messages_never_escape(tmp_path):
    root = copy_shop(tmp_path)
    with (root / "shop/cart.py").open("a") as stream:
        stream.write(
            "\ndef broken(self): raise RuntimeError('private traceback')\nCart.total=broken\n"
        )
    async with launched(tmp_path, root=root) as child:
        reply = await child.request()
        assert reply.status == 500 and reply.body == b"Preview request refused.\n"
        assert not child.protocol.output[1] and not child.protocol.output[2]


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "multipart/form-data", "application/x-www-form-urlencoded; charset=utf-8"],
)
async def test_only_exact_urlencoded_content_type(tmp_path, content_type):
    async with launched(tmp_path) as child:
        headers = [("Host", f"127.0.0.1:{child.port}"), ("Origin", "null"), ("Content-Length", "0")]
        if content_type:
            headers.append(("Content-Type", content_type))
        assert (await child.request("POST", "/cart", headers=headers)).status == 400


async def test_fresh_process_has_fresh_nonce_and_coupon_cache(tmp_path):
    root = copy_shop(tmp_path)
    async with launched(tmp_path, root=root) as child:
        before = await post(child, nonce(await child.request()), coupon="DEMO10")
        assert b'data-total-cents="1500"' in before.body
    pricing = root / "shop/pricing.py"
    pricing.write_text(
        pricing.read_text().replace('"SPRING10": 10}', '"SPRING10": 10, "DEMO10": 10}')
    )
    async with launched(tmp_path, root=root) as child:
        initial = await child.request()
        assert nonce(initial) != nonce(before)
        assert (await post(child, nonce(before), coupon="DEMO10")).status == 403
        after = await post(child, nonce(initial), coupon="DEMO10")
        assert b'data-total-cents="1350"' in after.body
