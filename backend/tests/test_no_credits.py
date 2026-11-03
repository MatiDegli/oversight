"""The global guard of the root conftest.py: no test reaches Meta's API or holds a provider key."""

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from oversight.agents import META_BASE_URL, MUSE_SPARK, OpenAIClient


def causes(error: BaseException) -> list[str]:
    names: list[str] = []
    current: BaseException | None = error
    while current is not None:
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return names


def test_a_meta_client_cannot_reach_meta_from_a_test() -> None:
    client = OpenAIClient(
        MUSE_SPARK, base_url=META_BASE_URL, api_key="test-key", max_output_tokens=16
    )
    # The SDK passes the guard's error through as it is, not as a ModelAPIError.
    with pytest.raises(Exception) as caught:
        asyncio.run(client.complete([{"role": "user", "content": "hello"}], []))
    assert "NetworkBlocked" in causes(caught.value)


def test_plain_httpx_cannot_leave_the_machine_either() -> None:
    with pytest.raises(Exception) as caught:
        httpx.get("https://example.invalid/", timeout=1)
    assert "NetworkBlocked" in causes(caught.value)


def test_an_exported_provider_key_is_absent_in_a_test() -> None:
    # Run with META_API_KEY=test-key exported: the guard drops it before every test.
    assert "META_API_KEY" not in os.environ
    assert "BASETEN_API_KEY" not in os.environ


@pytest.mark.parametrize("module_name", ["httpx", "httpx2"])
@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("host", ["example.invalid", "localhost", "127.0.0.1", "[::1]"])
def test_every_guarded_transport_checks_the_host_before_sending(
    module_name: str, mode: str, host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module(module_name)
    request = module.Request("GET", f"http://{host}/")
    remote = host == "example.invalid"
    # Replace the pool below the guarded method: even a broken guard cannot open a socket.
    reached = AssertionError("transport pool reached")
    send = Mock(side_effect=reached) if mode == "sync" else AsyncMock(side_effect=reached)
    expected = RuntimeError if remote else AssertionError
    message = "tests may not reach example.invalid" if remote else "transport pool reached"
    if mode == "sync":
        with module.HTTPTransport() as transport:
            monkeypatch.setattr(transport._pool, "handle_request", send)
            with pytest.raises(expected, match=message) as caught:
                transport.handle_request(request)
    else:

        async def send_async() -> None:
            async with module.AsyncHTTPTransport() as transport:
                monkeypatch.setattr(transport._pool, "handle_async_request", send)
                await transport.handle_async_request(request)

        with pytest.raises(expected, match=message) as caught:
            asyncio.run(send_async())
    if remote:
        assert "NetworkBlocked" in causes(caught.value)
        send.assert_not_called()
    else:
        send.assert_called_once()
        if isinstance(send, AsyncMock):
            send.assert_awaited_once()


def test_a_child_pytest_removes_inherited_provider_keys() -> None:
    test_file = Path(__file__).resolve()
    # Verify the made-up keys reached the child before pytest installs the real root guard.
    bootstrap = """
import os
import sys
import pytest

assert os.environ['META_API_KEY'] == 'made-up-meta-test-key'
assert os.environ['BASETEN_API_KEY'] == 'made-up-baseten-test-key'
raise SystemExit(pytest.main(sys.argv[1:]))
"""

    async def run_child() -> None:
        # Bypass run_process's Windows key stripping to exercise pytest's guard itself.
        child = await asyncio.create_subprocess_exec(  # noqa: TID251
            sys.executable,
            "-c",
            bootstrap,
            f"{test_file}::test_an_exported_provider_key_is_absent_in_a_test",
            "-o",
            "addopts=",
            "-q",
            "-p",
            "no:cacheprovider",
            cwd=test_file.parents[2],
            env={
                **os.environ,
                "META_API_KEY": "made-up-meta-test-key",
                "BASETEN_API_KEY": "made-up-baseten-test-key",
                "PYTEST_ADDOPTS": "",
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=30)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
        assert child.returncode == 0, (stdout + stderr).decode()
        assert "1 passed" in stdout.decode()

    asyncio.run(run_child())
