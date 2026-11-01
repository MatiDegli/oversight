"""Injected NT own-source policy, without enabling public source selection."""

import hashlib
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oversight.workspace import (
    IntegrityError,
    ProcessResult,
    Workspace,
    core,
    run_process,
    test_tree,
    validate_source_byte_policy,
)
from oversight.workspace.integrity import BYTE_OVERRIDE
from oversight.workspace.test_tree import capture_test_tree

pytestmark = pytest.mark.anyio


async def opened(tmp_path: Path, files: dict[str, bytes], autocrlf: str | None = None) -> Workspace:
    source = tmp_path / "source"
    source.mkdir()
    for name, data in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    workspace = Workspace(tmp_path / ".runtime", source, pytest_pin="9.1.1", _nt_source_bytes=True)
    await workspace.acquire_runtime()
    try:
        if autocrlf is not None:
            result = await run_process(
                ["git", "init", "-q", "--template="], cwd=source, timeout_s=10
            )
            assert result.returncode == 0
            config = source / ".git/config"
            config.write_bytes(
                config.read_bytes()
                + (
                    f"\n[core]\n autocrlf = {autocrlf}\n worktree = /missing\n"
                    '[filter "evil"]\n clean = touch FILTER_SENTINEL\n'
                ).encode()
            )
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        await workspace.reset()
        assert before == {
            p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
        }
    except BaseException:
        workspace.release_runtime()
        raise
    return workspace


@pytest.mark.parametrize("autocrlf", ["true", "false"])
async def test_initial_bytes_and_shared_override(tmp_path: Path, autocrlf: str) -> None:
    files = {
        ".gitattributes": b"* text=auto\r\n*.py text\n*.bin binary\n",
        "nested/.gitattributes": b"* eol=crlf\r\n",
        "nested/a.py": b"# unicode: \xc3\xa9\r\nx = 1\r\n# mixed\n",
        "data.bin": b"\x00\xff\r\n\x80",
    }
    # Source config is deliberately misleading and contains a filter that must never run.
    workspace = await opened(tmp_path, files, autocrlf)
    try:
        source = tmp_path / "source"
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        validate_source_byte_policy(source, source / ".git", tuple(files), time.monotonic() + 10)
        agent = await workspace.create_worktree("agent-1")
        assert agent.git_identity.common == workspace.main.git_identity.common
        assert (agent.git_identity.common / "info/attributes").read_bytes() == BYTE_OVERRIDE
        for name, data in files.items():
            assert (workspace.main.root / name).read_bytes() == data
            assert (agent.root / name).read_bytes() == data
            async with workspace.locks(main=True):
                oid = (await workspace.git_ok(workspace.main, "rev-parse", f"HEAD:{name}")).strip()
                # hash-object --stdin would need an input route; use Git's blob hash directly.
                assert oid == hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        assert before == {
            p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
        }
        assert not (source / "FILTER_SENTINEL").exists()
    finally:
        workspace.release_runtime()


@pytest.mark.parametrize(
    "policy",
    [
        b"* filter=evil",
        b"* working-tree-encoding=UTF-16",
        b"* ident",
        b"[attr]hidden filter=evil\n* hidden",
        b"[attr]a b\n[attr]b a\n* a",
    ],
)
async def test_transforms_refuse_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: bytes
) -> None:
    workspace = await opened(tmp_path, {"a.py": b"x=1\r\n"})
    try:
        nested = workspace.main.root / "nested"
        nested.mkdir()
        (nested / ".gitattributes").write_bytes(policy)
        spawn = AsyncMock()
        monkeypatch.setattr(core, "run_process", spawn)
        monkeypatch.setattr(test_tree, "run_process", spawn)
        async with workspace.locks(main=True):
            for args in [("add", "-A"), ("status", "--porcelain"), ("rev-parse", "HEAD")]:
                with pytest.raises(IntegrityError, match=r"nested/\.gitattributes"):
                    await workspace.git(workspace.main, *args)
        with pytest.raises(IntegrityError, match=r"nested/\.gitattributes"):
            await capture_test_tree(
                workspace.main.root,
                workspace.tmp_dir,
                env=workspace._git_env(workspace.main, None, None),
                flags=workspace.git_flags,
                timeout_s=10,
                nt_source_bytes=True,
            )
        spawn.assert_not_awaited()
    finally:
        workspace.release_runtime()


@pytest.mark.parametrize("tamper", ["missing", "altered", "symlink", "hardlink", "directory"])
async def test_common_override_tampering_refuses_every_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    workspace = await opened(tmp_path, {"a.py": b"x=1\r\n"})
    try:
        agent = await workspace.create_worktree("agent-1")
        override = agent.git_identity.common / "info/attributes"
        override.unlink()
        if tamper == "altered":
            override.write_bytes(b"* text\n")
        elif tamper in {"symlink", "hardlink"}:
            target = tmp_path / "external"
            target.write_bytes(BYTE_OVERRIDE)
            if tamper == "symlink":
                override.symlink_to(target)
            else:
                override.hardlink_to(target)
        elif tamper == "directory":
            override.mkdir()
        spawn = AsyncMock()
        monkeypatch.setattr(core, "run_process", spawn)
        monkeypatch.setattr(test_tree, "run_process", spawn)
        for tree in [workspace.main, agent]:
            async with workspace.locks(main=tree.agent_id is None, agent_id=tree.agent_id):
                with pytest.raises(IntegrityError, match="attributes"):
                    await workspace.git(tree, "rev-parse", "HEAD")
            with pytest.raises(IntegrityError, match="attributes"):
                await capture_test_tree(
                    tree.root,
                    workspace.tmp_dir,
                    env=workspace._git_env(tree, None, None),
                    flags=workspace.git_flags,
                    timeout_s=10,
                    nt_source_bytes=True,
                )
        spawn.assert_not_awaited()
    finally:
        workspace.release_runtime()


def test_source_info_policy_and_excluded_manifest(tmp_path: Path) -> None:
    common = tmp_path / ".git"
    (common / "info").mkdir(parents=True)
    policy = common / "info/attributes"
    policy.write_bytes(b"# even comments are nonempty\n")
    with pytest.raises(IntegrityError, match="source info attributes unsupported"):
        validate_source_byte_policy(tmp_path, common, (), time.monotonic() + 10)
    policy.write_bytes(b"")
    excluded = tmp_path / "excluded"
    excluded.mkdir()
    (excluded / ".gitattributes").write_bytes(b"* filter=evil")
    validate_source_byte_policy(tmp_path, common, (), time.monotonic() + 10)
    with pytest.raises(IntegrityError, match=r"excluded/\.gitattributes"):
        validate_source_byte_policy(
            tmp_path, common, ("excluded/.gitattributes",), time.monotonic() + 10
        )


async def test_installation_precedes_first_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def observe(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = 262_144,
    ) -> ProcessResult:
        if "add" in argv:
            assert (
                tmp_path / ".runtime/workspace/.git/info/attributes"
            ).read_bytes() == BYTE_OVERRIDE
            calls.append("add")
        return await run_process(argv, cwd=cwd, timeout_s=timeout_s, env=env, output_cap=output_cap)

    monkeypatch.setattr(core, "run_process", observe)
    workspace = await opened(tmp_path, {"a.py": b"x=1\r\n"})
    workspace.release_runtime()
    assert calls == ["add"]
