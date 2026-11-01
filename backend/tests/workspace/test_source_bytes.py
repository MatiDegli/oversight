"""Injected NT own-source policy, without enabling public source selection."""

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oversight.agents.tools import Toolbox
from oversight.workspace import (
    IntegrityError,
    ProcessResult,
    StaleFile,
    Workspace,
    content_hash,
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
            for command in [("add", "-A"), ("commit", "-q", "-m", "Source baseline")]:
                result = await run_process(
                    [
                        "git",
                        "-c",
                        f"core.autocrlf={autocrlf}",
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        *command,
                    ],
                    cwd=source,
                    timeout_s=10,
                )
                assert result.returncode == 0, result.stderr
            config = source / ".git/config"
            config.write_bytes(
                config.read_bytes()
                + (
                    f"\n[core]\n autocrlf = {autocrlf}\n worktree = /missing\n"
                    '[filter "evil"]\n clean = touch FILTER_SENTINEL\n'
                ).encode()
            )
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        if autocrlf is not None:
            validate_source_byte_policy(
                source, source / ".git", tuple(files), time.monotonic() + 10
            )
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
        "crlf.txt": b"a\r\nb\r\n",
        "lf.txt": b"a\nb\n",
    }
    # Source config is deliberately misleading and contains a filter that must never run.
    workspace = await opened(tmp_path, files, autocrlf)
    try:
        source = tmp_path / "source"
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        # Change only the stat cache relationship; validation must not refresh source's index.
        (source / "nested/a.py").write_bytes(files["nested/a.py"])
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
        b"* ident\0hidden",
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


@pytest.mark.parametrize("autocrlf", ["true", "false"])
async def test_edit_merge_export_byte_parity(tmp_path: Path, autocrlf: str) -> None:
    """Exercise runtime merge seams directly; public selection/Approve startup stays closed."""
    files = {
        ".gitattributes": b"* text=auto\n*.py text\n*.bin binary\n",
        "nested/.gitattributes": b"* eol=crlf\r\n",
        "nested/a.py": "# é\r\nvalue = 1\r\n# mixed\n".encode(),
        "old.bin": b"\x00\xff\r\n",
    }
    workspace = await opened(tmp_path, files, autocrlf)
    try:
        base = await workspace.main.head()
        agent = await workspace.create_worktree("agent-1")
        tools = Toolbox(workspace, agent, test_timeout_s=10)
        path = "nested/a.py"
        await tools.call("read_file", json.dumps({"path": path}))
        request = json.dumps(
            {"path": path, "old_text": "value = 1\n# mixed", "new_text": "value = 2\n# changed"}
        )
        assert (await tools.call("edit_file", request)).ok
        raw = "# é\r\nvalue = 2\r\n# changed\n".encode()
        assert (agent.root / path).read_bytes() == raw
        with pytest.raises(StaleFile):
            await agent.write_text(path, "stale", expected_hash=content_hash(files[path]))
        assert (await agent.read_text(path)).hash == content_hash(raw)
        (agent.root / "old.bin").unlink()
        binary = b"\x00\xfe\r\n\x80\n"
        (agent.root / "new.bin").write_bytes(binary)
        await agent.write_text("added.txt", "new\r\nline\n", expected_hash=None)
        head = await agent.commit_all("Byte edit", workspace.agent_author("agent-1"))
        assert head is not None
        # Fake evidence names the exact candidate; capture does not alter the real index.
        index = agent.git_identity.directory / "index"
        before = index.read_bytes()
        tested, commit = await capture_test_tree(
            agent.root,
            workspace.tmp_dir,
            env=workspace._git_env(agent, None, None),
            flags=workspace.git_flags,
            timeout_s=10,
            nt_source_bytes=True,
        )
        assert commit == head and index.read_bytes() == before
        async with workspace.locks(main=True, agent_id="agent-1"):
            assert tested == (await workspace.git_ok(agent, "rev-parse", "HEAD^{tree}")).strip()
            await workspace.git_ok(
                workspace.main, "merge", "--no-ff", "-m", "Approved byte fixture", head
            )
            patch = await workspace.git_ok(workspace.main, "diff", "--binary", base, "HEAD")
        expected = files | {path: raw, "new.bin": binary, "added.txt": b"new\r\nline\n"}
        del expected["old.bin"]
        clone = tmp_path / "export-target"
        result = await run_process(
            [
                "git",
                "clone",
                "-q",
                "--no-checkout",
                "--no-hardlinks",
                str(workspace.main.root),
                str(clone),
            ],
            cwd=tmp_path,
            timeout_s=10,
        )
        assert result.returncode == 0, result.stderr
        (clone / ".git/info/attributes").write_bytes(BYTE_OVERRIDE)
        patch_file = tmp_path / "export.patch"
        patch_file.write_bytes(patch.encode())
        for args in [("checkout", "-q", "--detach", base), ("apply", "--binary", str(patch_file))]:
            result = await run_process(["git", *args], cwd=clone, timeout_s=10)
            assert result.returncode == 0, result.stderr
        assert not (clone / "old.bin").exists()
        for name, data in expected.items():
            assert (workspace.main.root / name).read_bytes() == data
            assert (clone / name).read_bytes() == data
            async with workspace.locks(main=True):
                oid = (await workspace.git_ok(workspace.main, "rev-parse", f"HEAD:{name}")).strip()
            assert oid == hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    finally:
        workspace.release_runtime()


async def test_detached_combined_policy_before_object_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oversight.workspace import base_probe_checkout

    workspace = await opened(tmp_path, {"a.py": b"x=1\r\n"})
    try:
        agent = await workspace.create_worktree("agent-1")
        head = await agent.head()
        async with (
            workspace.locks(main=True, agent_id="agent-1"),
            base_probe_checkout(
                workspace, agent=agent, base=head, head=head, paths=(), timeout_s=10
            ) as combined,
        ):
            assert combined.git_identity.common == agent.git_identity.common
            policy = combined.root / ".gitattributes"
            policy.write_bytes(b"* filter=evil\n")
            try:
                with monkeypatch.context() as patch:
                    spawn = AsyncMock()
                    patch.setattr(core, "run_process", spawn)
                    with pytest.raises(IntegrityError, match="transform forbidden"):
                        await workspace.git(combined, "rev-parse", "HEAD")
                    spawn.assert_not_awaited()
            finally:
                policy.unlink()
    finally:
        workspace.release_runtime()


async def test_source_transform_never_refreshes_index_or_runs_filter(tmp_path: Path) -> None:
    workspace = await opened(tmp_path, {"a.py": b"x=1\r\n", ".gitattributes": b"* text\n"}, "true")
    try:
        source = tmp_path / "source"
        # A stat-dirty source with a real HEAD/index and misleading core.worktree.
        (source / "a.py").write_bytes(b"x=2\r\n")
        (source / ".gitattributes").write_bytes(b"* filter=evil\n")
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        with pytest.raises(IntegrityError, match="transform forbidden"):
            validate_source_byte_policy(
                source, source / ".git", ("a.py", ".gitattributes"), time.monotonic() + 10
            )
        assert before == {
            p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
        }
        assert not (source / "FILTER_SENTINEL").exists()
        assert not (workspace.main.root / "FILTER_SENTINEL").exists()
    finally:
        workspace.release_runtime()
