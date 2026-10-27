import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Async tests run on asyncio, the loop the server uses."""
    return "asyncio"
