"""Runtime tree route, bounded off-loop walk and the published contract validators."""

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest
from live_helpers import DEADLINE_S, error_code, is_type, live_server, live_settings, start
from pydantic import ValidationError

from oversight.workspace.tree import HASH_FILE_BYTES, HASH_TOTAL_BYTES, walk_tree
from oversight_contracts.commands import AgentCreated, TreeFile, TreeResponse

pytestmark = pytest.mark.anyio


def listing(root: Path) -> TreeResponse:
    directories, files, truncated = walk_tree(root, time.monotonic() + 2)
    return TreeResponse(
        run_id="run-20261028-000001",
        agent_id=None,
        directories=directories,
        files=files,
        graph_version=None,
        truncated=truncated,
    )


async def test_tree_on_main_and_agent_is_sorted_bounded_and_guarded(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as server:
        root = server.live.workspace.main.root
        (root / "z.py").write_bytes(b"z\r\n")
        (root / "a").mkdir()
        (root / "a" / ".git").mkdir()
        (root / "a" / ".git" / "secret").write_text("hidden")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.py").write_text("outside")
        (root / "link").symlink_to(outside, target_is_directory=True)
        (root / "link.py").symlink_to(outside / "secret.py")
        os.mkfifo(root / "pipe")
        response = await server.client.get("/tree")
        assert response.status_code == 200, response.text
        tree = TreeResponse.model_validate(response.json())
        assert tree.agent_id is None and tree.graph_version is None
        paths = [file.path for file in tree.files]
        assert paths == sorted(set(paths)) and tree.directories == sorted(set(tree.directories))
        assert not any(".git" in path.split("/") for path in paths + tree.directories)
        assert not {"link", "link.py", "pipe", "secret.py"}.intersection(paths + tree.directories)
        assert (
            next(file.hash for file in tree.files if file.path == "z.py")
            == hashlib.sha256(b"z\r\n").hexdigest()
        )
        assert not tree.truncated
        for agent in (
            "../outside",
            "/tmp/outside",
            "agent-1/../../outside",
            "agent-0",
            "agent-10000",
        ):
            assert error_code(await server.client.get("/tree", params={"agent": agent})) == (
                422,
                "invalid_request",
            )
        assert error_code(await server.client.get("/tree?agent=agent-999")) == (404, "not_found")
        assert error_code(await server.client.get("/tree?agent=agent-9999")) == (404, "not_found")
        assert (
            await server.client.get("/tree", headers={"Host": "evil.invalid"})
        ).status_code == 403
        assert (
            await server.client.get("/tree", headers={"Origin": "http://evil.invalid"})
        ).status_code == 403
        # C2a allows listing a nested .git safely, but refuses Git operations while it exists.
        assert error_code(await start(server)) == (500, "internal")
        assert any(
            event.type == "error" and "nested or linked Git entry" in event.payload.message
            for event in server.events()
        )
        (root / "a" / ".git" / "secret").unlink()
        (root / "a" / ".git").rmdir()
        started = await start(server)
        assert started.status_code == 201, (started.text, server.events())
        created = AgentCreated.model_validate(started.json())
        await server.until(is_type("agent.status", state="ready_for_review"))
        agent_tree = TreeResponse.model_validate(
            (await server.client.get("/tree", params={"agent": created.agent_id})).json()
        )
        assert agent_tree.agent_id == created.agent_id and agent_tree.graph_version is None
        main_hash = next(file.hash for file in tree.files if file.path == "shop/pricing.py")
        assert (
            next(file.hash for file in agent_tree.files if file.path == "shop/pricing.py")
            != main_hash
        )
        agent_root = server.live.workspace.worktree(created.agent_id).root
        agent_root.rename(agent_root.with_name("displaced"))
        agent_root.symlink_to(outside, target_is_directory=True)
        assert error_code(await server.client.get("/tree", params={"agent": created.agent_id})) == (
            422,
            "invalid_path",
        )
        agent_root.unlink()
        agent_root.with_name("displaced").rename(agent_root)


@pytest.mark.parametrize("count", [1999, 2000, 2001])
def test_entry_cap_and_exact_truncation(tmp_path: Path, count: int) -> None:
    (tmp_path / "folder").mkdir()
    for index in range(count - 1):
        (tmp_path / "folder" / f"{index:04}.py").touch()
    tree = listing(tmp_path)
    assert len(tree.directories) + len(tree.files) == min(count, 2000)
    assert tree.truncated == (count > 2000)


def test_hash_caps_and_exact_file_bytes(tmp_path: Path) -> None:
    (tmp_path / "large").write_bytes(b"x" * (HASH_FILE_BYTES + 1))
    for index in range(HASH_TOTAL_BYTES // HASH_FILE_BYTES + 1):
        (tmp_path / f"file-{index}").write_bytes(b"x" * HASH_FILE_BYTES)
    tree = listing(tmp_path)
    assert next(file.hash for file in tree.files if file.path == "large") is None
    assert sum(file.hash is not None for file in tree.files) == 8
    assert all(
        file.hash in (None, hashlib.sha256(b"x" * HASH_FILE_BYTES).hexdigest())
        for file in tree.files
    )


def test_expired_walk_fails_instead_of_claiming_truncation(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError):
        walk_tree(tmp_path, time.monotonic() - 1)


async def test_tree_timeout_is_off_loop_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oversight.api.commands as commands
    import oversight.workspace.tree as module

    real_walk = module.walk_tree

    def delayed(root: Path, deadline: float):
        time.sleep(0.08)
        return real_walk(root, deadline)

    monkeypatch.setattr(module, "TREE_TIMEOUT_S", 0.03)
    monkeypatch.setattr(commands, "TREE_TIMEOUT_S", 0.03, raising=False)
    monkeypatch.setattr(module, "walk_tree", delayed)
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as server:
        pending = asyncio.create_task(server.client.get("/tree"))
        assert (await server.client.get("/health")).status_code == 200
        response = await pending
        assert error_code(response) == (500, "internal")
        assert response.json()["error"]["message"] == (
            "listing files exceeded 0.03 seconds; retry the Files sidebar"
        )
        await asyncio.sleep(0.1)  # let the cooperative worker observe its deadline and close fds


def test_unreadable_subtree_does_not_hide_readable_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "hidden.py").touch()
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "visible.py").touch()
    real_open = os.open

    def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "private" and dir_fd is not None:
            raise PermissionError("unreadable directory")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", guarded_open)
    tree = listing(tmp_path)
    assert tree.directories == ["public"]
    assert [file.path for file in tree.files] == ["public/visible.py"]
    assert not tree.truncated


@pytest.mark.parametrize("hash_value", ["A" * 64, "f" * 63, "z" * 64])
def test_tree_file_rejects_invalid_hash(hash_value: str) -> None:
    with pytest.raises(ValidationError):
        TreeFile(path="a.py", hash=hash_value)


@pytest.mark.parametrize(
    "patch",
    [
        {"directories": ["b", "a"]},
        {"directories": ["a", "a"]},
        {"files": [{"path": "b", "hash": None}, {"path": "a", "hash": None}]},
        {"files": [{"path": "a", "hash": None}] * 2},
        {"directories": ["a"], "files": [{"path": "a", "hash": None}]},
        {"directories": [str(index) for index in range(2001)]},
        {"truncated": True},
    ],
)
def test_tree_contract_validators(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TreeResponse.model_validate(
            {
                "run_id": "run-20261028-000001",
                "agent_id": None,
                "directories": [],
                "files": [],
                "graph_version": None,
                "truncated": False,
                **patch,
            }
        )
