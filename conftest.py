"""Repository-wide test fixtures."""

import os
import sys
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest

# The memory cap (tools/memwatch.py) is a plugin of every run: make check, CI and a bare `pytest`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
pytest_plugins = ["memwatch"]


@pytest.fixture(autouse=True)
def _no_inherited_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop GIT_* variables, so a test's git commands touch only the repository it names.

    Git exports GIT_DIR and others to its hooks. The pre-push hook runs make check, and a test that
    ran `git init` or `git commit` in a temporary directory under that environment once wrote to
    this repository instead: it set core.bare and a test identity, and committed on the branch.
    """
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)


LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
PROVIDER_KEYS = ("META_API_KEY", "BASETEN_API_KEY")


class NetworkBlocked(RuntimeError):
    """A test tried to reach a host other than this machine: no test spends credits."""


def _check_host(request: httpx.Request | httpx2.Request) -> None:
    if request.url.host not in LOOPBACK:
        raise NetworkBlocked(f"tests may not reach {request.url.host}")


@pytest.fixture(autouse=True)
def _no_credits(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches Meta's API, or any host but this one: the provider keys are dropped from the
    environment, and the real transports of httpx2 (the OpenAI SDK's) and httpx refuse every
    other host. Mock transports, the probe's fake transport and loopback servers still work."""
    for name in PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
    for module in (httpx, httpx2):
        sync_send = module.HTTPTransport.handle_request
        async_send = module.AsyncHTTPTransport.handle_async_request

        def handle_request(self: Any, request: Any, _send: Any = sync_send) -> Any:
            _check_host(request)
            return _send(self, request)

        async def handle_async_request(self: Any, request: Any, _send: Any = async_send) -> Any:
            _check_host(request)
            return await _send(self, request)

        monkeypatch.setattr(module.HTTPTransport, "handle_request", handle_request)
        monkeypatch.setattr(module.AsyncHTTPTransport, "handle_async_request", handle_async_request)
