"""SQLite databases and live WAL sidecars are owner-only, regardless of the umask."""

import stat
from pathlib import Path

import pytest

from oversight.events import EventStore
from oversight.events.store import _connect
from oversight_contracts.events import AgentMessageSent

pytestmark = pytest.mark.anyio


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


async def test_fresh_store_modes_after_first_write(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "run.sqlite"
    async with EventStore(path, "run-20261027-140312") as store:
        assert mode(path.parent) == 0o700
        assert mode(path) == 0o600
        await store.publish(AgentMessageSent(agent_id="agent-1", text="Please check."))
        for suffix in ("", "-wal", "-shm"):
            assert mode(Path(str(path) + suffix)) == 0o600


async def test_existing_database_directory_and_sidecars_are_tightened(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "run.sqlite"
    connection = _connect(path)
    try:
        connection.execute("CREATE TABLE sample (value TEXT)")
        paths = [Path(str(path) + suffix) for suffix in ("", "-wal", "-shm")]
        for item in paths:
            item.chmod(0o666)
        path.parent.chmod(0o777)
        reopened = _connect(path)
        reopened.close()
        assert mode(path.parent) == 0o700
        assert [mode(item) for item in paths] == [0o600] * 3
    finally:
        connection.close()


async def test_refusing_existing_store_still_tightens_its_modes(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "run.sqlite"
    path.parent.mkdir(mode=0o755)
    path.touch(mode=0o644)
    with pytest.raises(FileExistsError):
        await EventStore(path, "run-20261027-140312").start()
    assert mode(path.parent) == 0o700 and mode(path) == 0o600
