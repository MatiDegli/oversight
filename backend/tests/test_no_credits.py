"""The global guard of the root conftest.py: no test reaches Meta's API or holds a provider key."""

import asyncio
import os

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
