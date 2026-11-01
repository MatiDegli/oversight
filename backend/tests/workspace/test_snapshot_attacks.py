"""Real pytest suites poison each metadata input; every subsequent server Git is observed."""

import asyncio
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from oversight.workspace import Workspace, core, run_process, test_tree
from oversight.workspace.integrity import IntegrityError
from oversight.workspace.snapshot import take_snapshot

pytestmark = pytest.mark.anyio

ATTACKS = (
    "config",
    "fsmonitor",
    "config.worktree",
    "config.worktree_present",
    "info",
    "hooks",
    "hooksPath",
    "hooksPath_config",
    "pointer",
    "admin",
    "HEAD",
    "main_ref",
    "packed-refs",
    "packed-refs_present",
    "alternates",
    "shallow",
    "replace",
    "index",
    "index_deleted",
    "index_absent",
    "MERGE_HEAD",
    "ORIG_HEAD",
    "gitdir",
    "commondir",
    "main_index",
    "info_symlink",
    "index.lock",
    "HEAD.lock",
    "refs/heads/main.lock",
)


async def attack_workspace(make_workspace: Any) -> Workspace:
    workspace = make_workspace(demo_python=Path(sys.executable))
    await workspace.acquire_runtime()
    await workspace.reset()
    return workspace


def monitor_git(workspace: Workspace, monkeypatch: pytest.MonkeyPatch, sentinel: Path) -> list[str]:
    calls: list[str] = []

    async def spawn(argv: list[str], **kwargs: Any) -> Any:
        result = await run_process(argv, **kwargs)
        if argv[0] == "git":
            command = argv[len(core.GIT_FLAGS) + 1]
            calls.append(command)
            assert not await asyncio.to_thread(sentinel.exists), (
                f"sentinel appeared after server git {command}"
            )
        return result

    monkeypatch.setattr(core, "run_process", spawn)
    monkeypatch.setattr(test_tree, "run_process", spawn)
    return calls


def attack_source(workspace: Workspace, attack: str, *, main: bool) -> str:
    tree = workspace.main if main else workspace.worktree("agent-1")
    identity = tree.git_identity
    common, admin = identity.common, identity.directory
    # Every attack also arms a clean filter. If metadata restoration is removed, a later
    # server add/capture executes it; testing only status/diff would miss this effect.
    poison = (
        '[filter "trap"]\n    clean = touch '
        + shlex.quote(str(workspace.tmp_dir / "git-sentinel"))
        + "; cat\n    required = true\n"
    )
    source = (
        "from pathlib import Path\nimport os\n\ndef test_poison_metadata():\n"
        f"    common = Path({str(common)!r})\n"
        f"    admin = Path({str(admin)!r})\n"
        f"    root = Path({str(tree.root)!r})\n"
        "    with (common / 'config').open('a') as handle:\n"
        f"        handle.write({poison!r})\n"
        "    (root / 'data.txt').write_text('modified\\n')\n"
    )
    payloads = attack_payloads(workspace, main=main)
    payload = (
        f"(common / {attack!r}).write_text('unknown owner\\n')"
        if attack.endswith(".lock")
        else payloads[attack.removesuffix("_present")]
    )
    return source + "\n".join("    " + row for row in payload.splitlines()) + "\n"


def attack_payloads(workspace: Workspace, *, main: bool) -> dict[str, str]:
    return {
        "config": "pass",
        "fsmonitor": (
            "with (common / 'config').open('a') as handle:\n"
            "    handle.write('[core]\\n    fsmonitor = untrusted\\n')"
        ),
        "config.worktree": (
            "(admin / 'config.worktree').write_bytes(b'[core]\\n    fsmonitor = trap\\n')"
        ),
        "info": "(common / 'info/exclude').write_text('poisoned\\n')",
        "hooks": (
            "(common / 'hooks').mkdir(exist_ok=True)\n"
            "(common / 'hooks/pre-commit').write_text('#!/bin/sh\\ntouch git-sentinel\\n')\n"
            "os.chmod(common / 'hooks/pre-commit', 0o755)"
        ),
        "hooksPath": (
            f"Path({str(workspace.runtime_root / 'custom-hooks/pre-commit')!r})"
            ".write_text('poisoned hook\\n')"
        ),
        "hooksPath_config": (
            "with (common / 'config').open('a') as handle:\n"
            "    handle.write('[core]\\n    hooksPath = /untrusted\\n')"
        ),
        "pointer": "(root / '.git').write_text('gitdir: /untrusted\\n')"
        if not main
        else (
            "common.rename(common.with_name('moved-admin'))\n"
            "common.write_text('gitdir: /untrusted\\n')"
        ),
        "admin": "admin.rename(admin.with_name('moved-admin'))\nadmin.mkdir()",
        "HEAD": "(admin / 'HEAD').write_text('ref: refs/heads/untrusted\\n')",
        "main_ref": "(common / 'refs/heads/main').write_text('1' * 40 + '\\n')",
        "packed-refs": "(common / 'packed-refs').write_text('1' * 40 + ' refs/heads/main\\n')",
        "alternates": "(common / 'objects/info/alternates').write_text('/untrusted\\n')",
        "shallow": "(common / 'shallow').write_text('1' * 40 + '\\n')",
        "replace": (
            "(common / 'refs/replace').mkdir(exist_ok=True)\n"
            "(common / 'refs/replace' / ('1' * 40)).write_text('2' * 40 + '\\n')"
        ),
        "index": "(admin / 'index').write_bytes(b'not an index')",
        "index_deleted": "(admin / 'index').unlink()",
        "index_absent": "(admin / 'index').write_bytes(b'an index created by the suite')",
        "MERGE_HEAD": "(admin / 'MERGE_HEAD').write_text('1' * 40 + '\\nextra\\n')",
        "ORIG_HEAD": "(admin / 'ORIG_HEAD').write_text('1' * 40 + '\\n')",
        "gitdir": "(admin / 'gitdir').write_text('/untrusted/.git\\n')",
        "commondir": "(admin / 'commondir').write_text('/untrusted\\n')",
        "main_index": "(common / 'index').write_bytes(b'main index tampered from another tree')",
        "info_symlink": (
            "(common / 'info/exclude').unlink()\n"
            "(common / 'info/exclude').symlink_to(root / 'data.txt')"
        ),
    }


@pytest.mark.parametrize("main", [False, True], ids=["agent", "main"])
@pytest.mark.parametrize("attack", ATTACKS)
async def test_each_suite_metadata_attack(
    make_workspace: Any, monkeypatch: pytest.MonkeyPatch, attack: str, main: bool
) -> None:
    workspace = await attack_workspace(make_workspace)
    tree = workspace.main if main else await workspace.create_worktree("agent-1")
    custom_hooks = workspace.runtime_root / "custom-hooks"
    custom_hooks.mkdir()
    (custom_hooks / "pre-commit").write_text("original hook\n")
    with (tree.git_identity.common / "config").open("a") as handle:
        handle.write(f'[core]\n    hooksPath = "{custom_hooks}"\n')
    # The attributes exist before the suite; capture is safe because trap is not configured.
    (tree.root / ".gitattributes").write_text("*.txt filter=trap\n")
    (tree.root / "data.txt").write_text("original\n")
    (tree.root / "tests/test_attack.py").write_text(attack_source(workspace, attack, main=main))
    if attack == "index_absent":
        (tree.git_identity.directory / "index").unlink()
    if attack == "config.worktree_present":
        for parent in {tree.git_identity.directory, tree.git_identity.common}:
            (parent / "config.worktree").write_bytes(b"[core]\n    fsmonitor = false\n")
    if attack == "packed-refs_present":
        (tree.git_identity.common / "packed-refs").write_text(
            f"{await workspace.main.head()} refs/heads/main\n"
        )
    baseline = take_snapshot(tree.git_identity, workspace.integrity, 5)
    sentinel = workspace.tmp_dir / "git-sentinel"
    calls = monitor_git(workspace, monkeypatch, sentinel)

    with pytest.raises(IntegrityError) as caught:
        await workspace.run_tests(tree, timeout_s=10, case_ids=("tests/test_attack.py",))
    assert calls and not sentinel.exists()
    assert workspace.integrity.rules_suspended
    uncertain = attack in {
        "admin",
        "info_symlink",
        "index.lock",
        "HEAD.lock",
        "refs/heads/main.lock",
    } or (main and attack == "pointer")
    assert workspace.integrity.main_suspect is uncertain, str(caught.value)
    assert caught.value.code == ("main_suspect" if uncertain else "integrity_invalid")
    if not uncertain:
        assert take_snapshot(tree.git_identity, workspace.integrity, 5).entries == baseline.entries
    before = len(calls)
    if uncertain:
        async with workspace.locks(main=True, agent_id=tree.agent_id):
            with pytest.raises(IntegrityError, match="code=main_suspect"):
                await workspace.git_ok(tree, "add", "-A")
        assert len(calls) == before
        if attack.endswith(".lock"):
            assert (tree.git_identity.common / attack).read_text() == "unknown owner\n"
    else:
        # Exercise write paths, not just status/diff, after verified restoration.
        await tree.commit_all("After restored suite", workspace.user_author())
        assert "add" in calls[before:] and "commit" in calls[before:]
        # A clean suite still runs; suspension never pretends isolation became unavailable.
        run = await workspace.run_tests(tree, timeout_s=10, case_ids=("tests/test_mod.py",))
        assert run.result.outcome == "passed", run.output
        assert workspace.integrity.rules_suspended and not workspace.integrity.main_suspect
    assert not sentinel.exists()


async def test_restore_is_needed_to_prevent_later_git_filter(
    make_workspace: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = await attack_workspace(make_workspace)
    tree = await workspace.create_worktree("agent-1")
    (tree.root / ".gitattributes").write_text("*.txt filter=trap\n")
    (tree.root / "data.txt").write_text("original\n")
    (tree.root / "tests/test_attack.py").write_text(attack_source(workspace, "config", main=False))
    sentinel = workspace.tmp_dir / "git-sentinel"
    calls = monitor_git(workspace, monkeypatch, sentinel)
    with pytest.raises(IntegrityError, match="code=integrity_invalid"):
        await workspace.run_tests(tree, timeout_s=10)
    await tree.commit_all("Must not execute the filter", workspace.user_author())
    assert "add" in calls and "commit" in calls
    assert not sentinel.exists()


@pytest.mark.parametrize("main", [False, True], ids=["agent", "main"])
@pytest.mark.parametrize("attack", ["config", "index", "replace", "info", "junction", "reparse"])
async def test_nt_suite_metadata_attacks(make_workspace, monkeypatch, main, attack):
    from .test_snapshot import force_nt, reparse_at

    workspace = await attack_workspace(make_workspace)
    tree = workspace.main if main else await workspace.create_worktree("agent-1")
    (tree.root / ".gitattributes").write_text("*.txt filter=trap\n")
    (tree.root / "data.txt").write_text("original\n")
    source = attack_source(
        workspace, "config" if attack in {"junction", "reparse"} else attack, main=main
    )
    (tree.root / "tests/test_attack.py").write_text(source)
    force_nt(monkeypatch)
    baseline = take_snapshot(tree.git_identity, workspace.integrity, 5)
    calls = monitor_git(workspace, monkeypatch, workspace.tmp_dir / "git-sentinel")
    if attack in {"junction", "reparse"}:
        from oversight.workspace.snapshot import MetadataSnapshot

        original = MetadataSnapshot.finish

        def injected(self, state, deadline):
            common = self.identity.common
            reparse_at(monkeypatch, common / ("info" if attack == "junction" else "config"))
            original(self, state, deadline)

        monkeypatch.setattr(MetadataSnapshot, "finish", injected)
    with pytest.raises(IntegrityError):
        await workspace.run_tests(tree, timeout_s=10, case_ids=("tests/test_attack.py",))
    assert calls and workspace.integrity.rules_suspended
    uncertain = attack in {"junction", "reparse"}
    assert workspace.integrity.main_suspect is uncertain
    before = len(calls)
    if uncertain:
        with pytest.raises(IntegrityError, match="code=main_suspect"):
            await tree.commit_all("Never spawn", workspace.user_author())
        assert len(calls) == before
    else:
        assert take_snapshot(tree.git_identity, workspace.integrity, 5).entries == baseline.entries
        await tree.commit_all("Restored metadata", workspace.user_author())
    assert not (workspace.tmp_dir / "git-sentinel").exists()
