"""Protected private reset and byte-preserving manual export, using throwaway sources."""

import hashlib
import re
import shutil
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oversight.settings import Settings
from oversight.workspace import Workspace, WorkspaceError, core, run_process
from oversight.workspace import source_nt as nt
from oversight.workspace.files import remove_tree
from oversight.workspace.integrity import BYTE_OVERRIDE
from oversight.workspace.locks import offload

from .test_source_nt_copy import fixture, git, stage

pytestmark = pytest.mark.anyio


async def test_unstaged_own_reset_refuses_before_any_effect(tmp_path, monkeypatch):
    root, runtime = tmp_path / "source", tmp_path / ".runtime"
    root.mkdir()
    original = root / "original.py"
    original.write_bytes(b"original\r\n")
    own = nt.NtSource(root, runtime, "C:/source", "C:/runtime")
    workspace = Workspace(runtime, root, pytest_pin="9.1.1", own_source=own, _nt_source_bytes=True)
    await workspace.acquire_runtime()
    sentinels = [runtime / name / "preserve" for name in ("workspace", "worktrees", "tmp")]
    for path in sentinels:
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"prior runtime")
    effects = []

    def forbidden(*args, **kwargs):
        effects.append("destructive/copy/Git")
        raise AssertionError("unstaged own reset reached an effect")

    monkeypatch.setattr(core, "remove_tree", forbidden)
    monkeypatch.setattr(core, "copy_template", forbidden)
    monkeypatch.setattr(workspace, "_populate", forbidden)
    monkeypatch.setattr(workspace, "git_ok", forbidden)
    try:
        with pytest.raises(WorkspaceError, match="own-source reset requires validated staging"):
            await workspace.reset()
        assert effects == []
        assert all(path.read_bytes() == b"prior runtime" for path in sentinels)
        assert original.read_bytes() == b"original\r\n"
    finally:
        workspace.release_runtime()


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
    workspace = Workspace(
        runtime, root, pytest_pin="9.1.1", _nt_source_bytes=True, own_source=source
    )
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


async def clone_baseline(source: nt.NtSource, target: Path, files: dict[str, bytes]) -> None:
    await git(target.parent, "clone", "-q", "--no-hardlinks", str(source.root), str(target))
    (target / "gone.py").unlink()
    for name, data in files.items():
        (target / name).write_bytes(data)
    assert await git(target, "rev-parse", "HEAD") == await git(source.root, "rev-parse", "HEAD")
    assert await git(target, "show", "HEAD:a.py") == "x=1"
    assert (target / "a.py").read_bytes() == b"dirty\r\n"
    assert not await git(target, "ls-files", "new.py")


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
            imported = dict(files)
            target = tmp_path / "baseline-clone"
            await clone_baseline(source, target, imported)
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
                "a.py": b"changed dirty baseline\r\n",
                "data.bin": b"\x00\xfe\r\n",
                "added.txt": b"added\r\n",
                "nested/code.py": b"value = 2\r\n",
            }
            for name in ("a.py", "data.bin", "added.txt", "nested/code.py"):
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
            assert b"diff --git a/a.py b/a.py" in await offload(patch_file.read_bytes)
            # Apply only to the clone with the imported dirty/untracked working bytes.
            await git(target, "apply", "--check", str(patch_file))
            assert {name: (target / name).read_bytes() for name in imported} == imported
            await git(target, "apply", str(patch_file))
            for name, data in files.items():
                assert (target / name).read_bytes() == data
            assert not (target / "new.py").exists()
            assert not (target / "uncommitted").exists()
            assert {name: (source.root / name).read_bytes() for name in imported} == imported
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
            # Match Git's LF bytes; native Windows text writes would introduce CRLF.
            ref_file.write_bytes(changed.encode("ascii") + b"\n")
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


@pytest.mark.parametrize(
    "phase",
    [
        "before",
        "partial-delete",
        "deleted",
        "published",
        "committed",
        "baseline",
        "unstaged-delete",
        "unstaged-populate",
        "unstaged-init",
    ],
)
async def test_interrupted_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    async with opened(tmp_path) as (workspace, source, _):
        own = not phase.startswith("unstaged-")
        new = await restart(workspace, source, own=own)
        try:
            assert new._lock_fd is not None
            staged = await stage(source, new._lock_fd) if own else None

            def fail(*args, **kwargs):
                raise OSError("interrupted reset")

            def partial_delete(root: Path, target: Path) -> None:
                if target == new.main.root:
                    # Runtime Git objects may carry the Windows read-only attribute.
                    for path in (target / ".git").rglob("*"):
                        if path.is_file():
                            path.chmod(0o600)
                    shutil.rmtree(target / ".git")
                    fail()
                remove_tree(root, target)

            def partial_populate(template: Path, destination: Path) -> None:
                destination.mkdir(exist_ok=True)
                (destination / "partial.py").write_bytes(b"partial copy\n")
                fail()

            spawn = AsyncMock(side_effect=fail)
            with monkeypatch.context() as patch:
                if phase == "baseline":
                    assert staged is not None
                    patch.setattr(staged, "cleanup", fail)
                elif phase in {"partial-delete", "unstaged-delete"}:
                    patch.setattr(core, "remove_tree", partial_delete)
                elif phase == "unstaged-populate":
                    patch.setattr(core, "copy_template", partial_populate)
                else:
                    target = {
                        "before": "remove_tree",
                        "deleted": "publish_stage",
                        "published": "run_process",
                        "committed": "finish_reset",
                        "unstaged-init": "run_process",
                    }[phase]
                    patch.setattr(core, target, spawn if target == "run_process" else fail)
                with pytest.raises(OSError, match="interrupted reset"):
                    await new.reset(_nt_stage=staged)
                if phase == "unstaged-init":
                    spawn.assert_awaited_once()
                    assert spawn.call_args is not None and "init" in spawn.call_args.args[0]
            assert not new._reset_done
            if phase in {"partial-delete", "unstaged-delete", "unstaged-populate", "unstaged-init"}:
                assert not (new.main.root / ".git").exists()
                new = await restart(new, source, own=own)
            if phase == "unstaged-populate":
                assert (new.main.root / "partial.py").read_bytes() == b"partial copy\n"
            if phase == "before":
                assert nt.main_ref(source.runtime, time.monotonic() + 10)
            if phase == "committed":
                with pytest.raises(
                    OSError,
                    match="Windows source refused: ambiguous interrupted reset; preserve runtime",
                ) as refused:
                    nt.guard_previous(source.runtime, recover=True)
                assert nt.ps_quote(source.runtime) in str(refused.value)
                assert "recover its files and ownership records manually before restarting" in str(
                    refused.value
                )
            else:
                assert nt.guard_previous(source.runtime, recover=True)
                if staged is not None:
                    assert not staged.root.exists()
                assert new._lock_fd is not None
                await new.reset(_nt_stage=await stage(source, new._lock_fd) if own else None)
                assert nt.guard_previous(source.runtime) is own
                if not own:
                    assert await new.main.head()
                    assert (new.main.root / "demo.py").read_bytes() == b"demo = True\n"
                    assert not (source.runtime / "own-reset").exists()
                    assert not (source.runtime / "own-runtime").exists()
        finally:
            new.release_runtime()


@pytest.mark.parametrize("changed_after_journal", [False, True])
async def test_journal_pins_authorized_pre_deletion_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_after_journal: bool
) -> None:
    async with opened(tmp_path) as (workspace, source, _):
        await git(workspace.main.root, "commit", "--allow-empty", "-qm", "Authorized delta")
        authorized = await workspace.main.head()
        monkeypatch.setenv("OVERSIGHT_DISCARD_OWN_RUNTIME", authorized)
        assert nt.guard_previous(source.runtime)
        nt.begin_reset(source.runtime, None)
        monkeypatch.delenv("OVERSIGHT_DISCARD_OWN_RUNTIME")
        if changed_after_journal:
            await git(workspace.main.root, "commit", "--allow-empty", "-qm", "Unexpected delta")
            with pytest.raises(OSError, match=r"main .* differs from initial"):
                nt.guard_previous(source.runtime, recover=True)
        else:
            assert nt.guard_previous(source.runtime, recover=True)
        assert nt.main_ref(source.runtime, time.monotonic() + 10) == await workspace.main.head()


@pytest.mark.parametrize("fault", ["bytes", "manifest", "deadline", "second-guard", "begin-reset"])
async def test_stage_refusal_cleans_before_journaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    async with opened(tmp_path) as (workspace, source, _):
        new = await restart(workspace, source, own=True)
        try:
            assert new._lock_fd is not None
            staged = await stage(source, new._lock_fd)
            before = {
                name: (source.runtime / name).read_bytes() for name in ("own-source", "own-runtime")
            }
            sibling = source.runtime / "source-stage-unowned"
            sibling.mkdir()
            (sibling / "keep").write_bytes(b"unowned")
            reason = "staging bytes changed" if fault == "bytes" else "staging manifest changed"
            if fault in {"second-guard", "begin-reset"}:
                guard = nt.guard_previous
                calls = 0

                def second_guard(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise OSError("second-guard refusal")
                    return guard(*args, **kwargs)

                def begin_refusal(*args):
                    raise OSError("begin-reset refusal")

                if fault == "second-guard":
                    monkeypatch.setattr(core, "guard_previous", second_guard)
                else:
                    monkeypatch.setattr(core, "begin_reset", begin_refusal)
                reason = fault + " refusal"
            elif fault == "deadline":
                verify = nt.verify_stage

                def expire_after_verification(*args):
                    verify(*args)
                    staged.deadline = 0

                monkeypatch.setattr(core, "verify_stage", expire_after_verification)
                reason = "COPY phase deadline exceeded"
            else:
                path = staged.tree / "a.py" if fault == "bytes" else staged.manifest
                path.write_bytes(b"tampered")
            spawn = AsyncMock(side_effect=AssertionError("runtime Git before refusal"))
            deletion = AsyncMock(side_effect=AssertionError("deletion before refusal"))
            monkeypatch.setattr(core, "run_process", spawn)
            monkeypatch.setattr(core, "remove_tree", deletion)
            with pytest.raises(OSError, match=reason):
                await new.reset(_nt_stage=staged)
            spawn.assert_not_awaited()
            deletion.assert_not_called()
            assert not new._reset_done
            assert not (source.runtime / "own-reset").exists()
            assert before == {name: (source.runtime / name).read_bytes() for name in before}
            assert not staged.root.exists()
            assert (workspace.main.root / "a.py").read_bytes() == b"dirty\r\n"
            assert (sibling / "keep").read_bytes() == b"unowned"
            assert nt.guard_previous(source.runtime)
        finally:
            new.release_runtime()
