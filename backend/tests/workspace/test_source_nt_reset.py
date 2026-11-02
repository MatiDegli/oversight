"""Protected private reset and byte-preserving manual export, using throwaway sources."""

import hashlib
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oversight.settings import Settings
from oversight.workspace import Workspace, core, run_process
from oversight.workspace import source_nt as nt
from oversight.workspace.integrity import BYTE_OVERRIDE
from oversight.workspace.locks import offload

from .test_source_nt_copy import fixture, git, stage

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("name", "escaped"),
    [
        ("Ana's", "Ana''s"),
        ("Ana\u2018s", "Ana\u2018\u2018s"),
        ("Ana\u2019s", "Ana\u2019\u2019s"),
        ("Ana\u201as", "Ana\u201a\u201as"),
        ("Ana\u201bs", "Ana\u201b\u201bs"),
    ],
)
async def test_recovery_quotes_powershell_paths(name: str, escaped: str) -> None:
    runtime = Path(f"C:/Users/{name}/runtime")
    source = Path(f"D:/Users/{name}/source")
    initial, current = "a" * 40, "b" * 40
    message = nt.recovery(runtime, [initial, str(source)], current)
    expected_runtime = Path(f"C:/Users/{escaped}/runtime")
    expected_source = Path(f"D:/Users/{escaped}/source")
    assert f"git -C '{expected_runtime / 'workspace'}' diff --binary" in message
    assert f"git -C '{expected_source}' apply --check '" in message
    assert f"then explicitly git -C '{expected_source}' apply '" in message
    patch = rf"{re.escape(str(expected_runtime.parent))}[/\\]oversight-export-[0-9a-f]{{32}}\.patch"
    assert re.search(rf"'--output={patch}' {initial} main", message)
    assert len(re.findall(rf"apply(?: --check)? '{patch}'", message)) == 2


@asynccontextmanager
async def opened(tmp_path: Path) -> AsyncGenerator[tuple[Workspace, nt.NtSource, dict[str, bytes]]]:
    root, runtime = tmp_path / "source", tmp_path / ".runtime"
    root.mkdir()
    source = nt.NtSource(root, runtime, "C:/source", "C:/runtime")
    files = await fixture(source)
    workspace = Workspace(runtime, root, pytest_pin="9.1.1", _nt_source_bytes=True)
    await workspace.acquire_runtime()
    try:
        assert workspace._lock_fd is not None
        await workspace.reset(_nt_stage=await stage(source, workspace._lock_fd))
        yield workspace, source, files
    finally:
        workspace.release_runtime()


async def restart(workspace: Workspace, source: nt.NtSource, *, own: bool = False) -> Workspace:
    workspace.release_runtime()
    template = source.root if own else source.runtime.parent / "other-profile"
    template.mkdir(exist_ok=True)
    if not own:
        (template / "demo.py").write_bytes(b"demo = True\n")
    new = Workspace(source.runtime, template, pytest_pin="9.1.1", _nt_source_bytes=own)
    await new.acquire_runtime()
    return new


async def test_manifest_reset_index_agent_and_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []
    original = run_process

    async def observe(argv, **kwargs):
        if "add" in argv:
            assert kwargs["env"].get("GIT_LITERAL_PATHSPECS") == "1"
            assert (Path(kwargs["cwd"]) / ".git/info/attributes").read_bytes() == BYTE_OVERRIDE
            calls.append(tuple(argv))
        return await original(argv, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(core, "run_process", observe)
        async with opened(tmp_path) as (workspace, source, files):
            assert len(calls) == 1 and calls[0][-4:-2] == ("add", "-f")
            assert calls[0][-2].startswith("--pathspec-from-file=")
            assert calls[0][-1] == "--pathspec-file-nul"
            patch.undo()
            initial = await workspace.main.head()
            assert nt.guard_previous(source.runtime)
            agent = await workspace.create_worktree("agent-1")
            for name, data in files.items():
                assert (
                    (workspace.main.root / name).read_bytes()
                    == (agent.root / name).read_bytes()
                    == data
                )
                async with workspace.locks(main=True):
                    oid = (await workspace.git_ok(workspace.main, "rev-parse", f":{name}")).strip()
                assert oid == hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            (workspace.main.root / "new.py").unlink()
            files.pop("new.py")
            files |= {
                "data.bin": b"\x00\xfe\r\n",
                "added.txt": b"added\r\n",
                "nested/code.py": b"value = 2\r\n",
            }
            for name in ("data.bin", "added.txt", "nested/code.py"):
                (workspace.main.root / name).write_bytes(files[name])
            await git(workspace.main.root, "add", "-A")
            await git(workspace.main.root, "commit", "-qm", "Runtime delta")
            current = await workspace.main.head()
            (workspace.main.root / "uncommitted").write_bytes(b"preserve runtime")
            with pytest.raises(OSError) as refused:
                nt.guard_previous(source.runtime)
            message = str(refused.value)
            assert "committed delta only" in message and "uncommitted" in message
            output = re.search(r"'--output=([^']+)'", message)
            assert output is not None
            patch_file = Path(output[1])
            assert patch_file.parent == source.runtime.parent
            assert not await offload(patch_file.exists)
            await git(
                workspace.main.root,
                "diff",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                f"--output={patch_file}",
                initial,
                "main",
            )
            # This source still has its imported dirty and untracked baseline bytes.
            before = (source.root / "data.bin").read_bytes()
            await git(source.root, "apply", "--check", str(patch_file))
            assert (source.root / "data.bin").read_bytes() == before
            await git(source.root, "apply", str(patch_file))
            for name, data in files.items():
                assert (source.root / name).read_bytes() == data
            assert not (source.root / "new.py").exists()
            assert (workspace.main.root / "uncommitted").exists()
            assert nt.main_ref(source.runtime, time.monotonic() + 10) == current


@pytest.mark.parametrize("ref", ["advanced", "rewound", "unrelated", "packed"])
@pytest.mark.parametrize("token", ["missing", "boolean", "stale", "exact", "dotenv"])
async def test_reset_refusal_before_git_or_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str, token: str
) -> None:
    async with opened(tmp_path) as (workspace, source, _):
        initial = await workspace.main.head()
        await git(workspace.main.root, "commit", "--allow-empty", "-qm", "Advance")
        advanced = await workspace.main.head()
        changed = (
            advanced if ref in {"advanced", "packed"} else ("a" if ref == "rewound" else "b") * 40
        )
        await git(workspace.main.root, "update-ref", "refs/heads/main", advanced)
        ref_file = workspace.main.root / ".git/refs/heads/main"
        if ref == "packed":
            await git(workspace.main.root, "pack-refs", "--all", "--prune")
        else:
            ref_file.write_text(changed + "\n")
        monkeypatch.delenv("OVERSIGHT_DISCARD_OWN_RUNTIME", raising=False)
        if token == "dotenv":
            env = tmp_path / "settings.env"
            env.write_text(f"OVERSIGHT_DISCARD_OWN_RUNTIME={changed}\n")
            Settings.load(env)
        elif token != "missing":
            monkeypatch.setenv(
                "OVERSIGHT_DISCARD_OWN_RUNTIME",
                {"boolean": "1", "stale": initial, "exact": changed}[token],
            )
        new = await restart(workspace, source)
        try:
            with monkeypatch.context() as patch:
                spawn = AsyncMock(side_effect=AssertionError("runtime Git before guard"))
                deletion = AsyncMock(side_effect=AssertionError("deletion before guard"))
                patch.setattr(core, "run_process", spawn)
                patch.setattr(core, "remove_tree", deletion)
                if token == "exact":
                    assert nt.guard_previous(source.runtime)
                else:
                    with pytest.raises(OSError, match=r"main .* differs from initial"):
                        await new.reset()
                spawn.assert_not_awaited()
                deletion.assert_not_called()
            assert ref_file.exists() or (workspace.main.root / ".git/packed-refs").exists()
            if token == "exact":
                await new.reset()
                assert not nt.guard_previous(source.runtime)
        finally:
            new.release_runtime()


@pytest.mark.parametrize(
    "damage",
    [
        "state",
        "missing-state",
        "state-link",
        "HEAD",
        "ref",
        "missing-ref",
        "packed",
        "identity",
        "incomplete",
    ],
)
async def test_unsafe_state_preserves_runtime(tmp_path: Path, damage: str) -> None:
    async with opened(tmp_path) as (workspace, source, _):
        state = source.runtime / "own-source"
        if damage == "state":
            state.write_bytes(b"corrupt")
        elif damage == "missing-state":
            state.unlink()
        elif damage == "state-link":
            backup = source.runtime / "backup"
            state.rename(backup)
            state.hardlink_to(backup)
        elif damage in {"HEAD", "ref", "missing-ref"}:
            path = (
                workspace.main.root / ".git" / ("HEAD" if damage == "HEAD" else "refs/heads/main")
            )
            path.unlink()
            if damage != "missing-ref":
                path.mkdir()
        elif damage == "packed":
            (workspace.main.root / ".git/packed-refs").write_bytes(b"broken\n")
        elif damage == "identity":
            workspace.main.root.rename(source.runtime / "saved")
            workspace.main.root.mkdir()
        else:
            (source.runtime / "own-reset").write_bytes(b"broken\0")
        with pytest.raises(OSError, match=r"preserve .*recover"):
            nt.guard_previous(source.runtime)
        assert workspace.main.root.exists()


@pytest.mark.parametrize("phase", ["before", "deleted", "published", "committed", "baseline"])
async def test_interrupted_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    async with opened(tmp_path) as (workspace, source, _):
        new = await restart(workspace, source, own=True)
        try:
            assert new._lock_fd is not None
            staged = await stage(source, new._lock_fd)

            def fail(*args, **kwargs):
                raise OSError("interrupted reset")

            with monkeypatch.context() as patch:
                if phase == "baseline":
                    patch.setattr(staged, "cleanup", fail)
                else:
                    target = {
                        "before": "remove_tree",
                        "deleted": "publish_stage",
                        "published": "run_process",
                        "committed": "finish_reset",
                    }[phase]
                    patch.setattr(
                        core, target, AsyncMock(side_effect=fail) if phase == "published" else fail
                    )
                with pytest.raises(OSError, match="interrupted reset"):
                    await new.reset(_nt_stage=staged)
            assert not new._reset_done
            if phase == "before":
                assert nt.main_ref(source.runtime, time.monotonic() + 10)
            if phase == "committed":
                with pytest.raises(OSError, match="preserve"):
                    nt.guard_previous(source.runtime, recover=True)
            else:
                assert nt.guard_previous(source.runtime, recover=True)
                assert not staged.root.exists()
                await new.reset(_nt_stage=await stage(source, new._lock_fd))
                assert nt.guard_previous(source.runtime)
        finally:
            new.release_runtime()
