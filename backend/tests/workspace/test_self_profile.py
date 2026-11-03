"""Reviewed self sources keep tracked inputs and demo suite evidence in disposable trees."""

import hashlib
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from workspace.test_preview_launch_spec import prepared_app

from oversight.app import LiveRun
from oversight.changes._runrule import Edit, RunTrace, Weakening, evaluate
from oversight.changes._runrule import TestRun as RunWitness
from oversight.changes._suite_evidence import publish_suite
from oversight.events import EventStore
from oversight.settings import Settings
from oversight.workspace import Workspace, run_process
from oversight.workspace import preview_profile as authority_module
from oversight.workspace import source as sources
from oversight.workspace.files import copy_template
from oversight.workspace.source import (
    OVERSIGHT_SELF,
    SELF_INVENTORY_REFUSAL,
    SELF_PIN_REFUSAL,
    OwnSource,
    SourceInventory,
    select_source,
    self_inventory,
)

pytestmark = pytest.mark.anyio
REPOSITORY = Path(__file__).resolve().parents[3]


async def git(root, *args):
    result = await run_process(["git", *args], cwd=root, timeout_s=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


async def self_checkout(tmp_path, monkeypatch):
    root, resources, _ = prepared_app(tmp_path)
    runtime = tmp_path / ".runtime"
    target = OVERSIGHT_SELF.checkout(runtime)
    target.parent.mkdir(parents=True)
    root.rename(target)
    files = {
        "demo-repo/pyproject.toml": '[tool.pytest.ini_options]\npythonpath=["."]\n',
        "demo-repo/shop/__init__.py": "",
        "demo-repo/shop/value.py": "def value():\n    return 42\n",
        "demo-repo/tests/test_value.py": (
            "from pathlib import Path\nimport shop.value as module\n"
            "def test_value():\n"
            "    assert Path(module.__file__).resolve().parent == "
            "Path(__file__).resolve().parents[1] / 'shop'\n"
            "    assert module.value() == 42\n"
        ),
        "tests/test_outside.py": "def test_outside():\n    assert False\n",
        "conftest.py": "raise RuntimeError('root conftest must not load')\n",
        ".gitignore": "generated/\n",
        ".env": "must never expose secret marker",
        "private.pem": "must never expose secret marker",
        ".env.example": "EXAMPLE=\n",
        "node_modules/unsafe.js": "dependency marker",
        ".runtime/state": "generated marker",
        "frontend/dist/old.js": "build marker",
    }
    for name, content in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    await git(target, "init", "-q")
    await git(target, "add", "-f", ".")
    await git(
        target,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "reviewed fixture",
    )
    profile = replace(OVERSIGHT_SELF, commit=await git(target, "rev-parse", "HEAD"))
    monkeypatch.setattr(sources, "OVERSIGHT_SELF", profile)
    monkeypatch.setattr(authority_module, "OVERSIGHT_SELF", profile)
    monkeypatch.setattr("oversight.app.OVERSIGHT_SELF", profile)
    return runtime, target, profile, resources


async def test_self_profile_pin_inventory_and_copy(tmp_path, monkeypatch):
    assert len(OVERSIGHT_SELF.commit) == 40
    assert select_source("oversight-self") == OVERSIGHT_SELF
    with pytest.raises(ValueError, match=SELF_PIN_REFUSAL):
        await self_inventory(OVERSIGHT_SELF, tmp_path)
    runtime, root, profile, _ = await self_checkout(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=SELF_PIN_REFUSAL):
        await self_inventory(replace(profile, commit="0" * 40), runtime)
    generated = root / "generated/untracked.txt"
    generated.parent.mkdir()
    generated.write_text("generated untracked marker")
    original = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    inventory = await self_inventory(profile, runtime)
    approved = {p.path.relative_to(root).as_posix() for p in inventory.entries}
    assert ".env.example" in approved and "demo-repo/shop/value.py" in approved
    assert not approved.intersection(
        {
            ".env",
            "private.pem",
            "node_modules/unsafe.js",
            ".runtime/state",
            "frontend/dist/old.js",
            "generated/untracked.txt",
        }
    )
    assert len(inventory.digest) == 64
    copy_template(root, tmp_path / "T", inventory=inventory)
    assert approved == {
        p.relative_to(tmp_path / "T").as_posix() for p in (tmp_path / "T").rglob("*") if p.is_file()
    }
    assert original == {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    (root / "demo-repo/shop/value.py").write_text("changed")
    with pytest.raises(ValueError, match=SELF_INVENTORY_REFUSAL):
        copy_template(root, tmp_path / "race", inventory=inventory)
    with pytest.raises(ValueError, match=SELF_PIN_REFUSAL):
        await self_inventory(profile, runtime)
    forged = SourceInventory(root, profile.commit, (), hashlib.sha256(b"").hexdigest())
    with pytest.raises(ValueError, match=SELF_INVENTORY_REFUSAL):
        forged.verify()


@pytest.mark.parametrize(
    "kind", ["link", "cycle", "submodule", "malformed", "truncated", "special"]
)
async def test_self_inventory_refuses_hostile_inputs(tmp_path, monkeypatch, kind):
    runtime, root, profile, _ = await self_checkout(tmp_path, monkeypatch)
    if kind in {"link", "cycle", "submodule"}:
        path = root / "hostile"
        if kind == "submodule":
            path.mkdir()
            await git(
                root, "update-index", "--add", "--cacheinfo", "160000", profile.commit, "hostile"
            )
        else:
            path.symlink_to("hostile" if kind == "cycle" else ".env")
            await git(root, "add", "hostile")
        await git(
            root,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "hostile fixture",
        )
        profile = replace(profile, commit=await git(root, "rev-parse", "HEAD"))
        monkeypatch.setattr(sources, "OVERSIGHT_SELF", profile)

    async def spawn(argv, **kwargs):
        result = await run_process(argv, **kwargs)
        if "ls-files" in argv:
            if kind == "malformed":
                return replace(result, stdout="bad\0")
            if kind == "truncated":
                return replace(result, truncated=True)
            if kind == "special":
                path = root / "demo-repo/shop/value.py"
                path.unlink()
                path.mkdir()
        return result

    with pytest.raises(ValueError, match=SELF_INVENTORY_REFUSAL):
        await self_inventory(profile, runtime, spawn=spawn)


async def test_self_profile_pytest_evidence_and_imports(tmp_path, monkeypatch):
    import pytest as prepared_pytest

    assert prepared_pytest.__version__ == "9.1.1"
    with pytest.raises(ValueError, match="oversight-self: prepared demo Python is required"):
        LiveRun(
            Settings(runtime_dir=tmp_path / "missing/.runtime", workspace_source="oversight-self")
        )
    runtime, root, profile, _ = await self_checkout(tmp_path, monkeypatch)
    inventory = await self_inventory(profile, runtime)
    workspace = Workspace(
        runtime,
        root,
        pytest_pin="9.1.1",
        demo_python=Path(sys.executable),
        source_inventory=inventory,
        require_demo_python=True,
        pytest_targets=profile.pytest_targets,
        pytest_options=profile.pytest_options,
    )
    await workspace.acquire_runtime()
    try:
        await workspace.reset()
        await workspace.ensure_demo_venv()
        assert workspace._demo_python == Path(sys.executable)
        assert not (runtime / "demo-venv").exists()
        first = await workspace.run_tests(workspace.main, timeout_s=30)
        node_id = "demo-repo/tests/test_value.py::test_value"
        assert first.result.outcome == "passed", first.output
        assert [c.test_id for c in first.cases] == [node_id]
        path = node_id.split("::")[0]
        copied_source = (workspace.main.root / path).read_text()
        async with workspace.locks(main=True):
            assert (
                await workspace.git_ok(workspace.main, "show", f"{first.tested_tree}:{path}")
                == copied_source
            )
        rec = SimpleNamespace(
            tree=workspace.main,
            base_files={},
            head_files={},
            suite_runs={},
            run_suites=[],
            executions={},
            base_commit=first.tested_commit,
            revision=0,
            change_id="change-1",
            agent_id="agent-1",
        )

        async def whole(work):
            return await work

        async def run(tree, *args):
            async with workspace.locks(main=True):
                return await workspace.git(tree, *args)

        async with EventStore(tmp_path / "e.sqlite", "run-20261103-000000", mode="replay") as store:
            ctx = SimpleNamespace(run=run, whole=whole, publish=store.publish)
            await publish_suite(ctx, rec, first, phase="agent", revision=1)  # pyright: ignore[reportArgumentType]
            assert rec.run_suites[0].sources == ((path, copied_source),)
        target = workspace.main.root / path
        target.write_text(copied_source.replace("== 42", "== 0"))
        failed = await workspace.run_tests(workspace.main, timeout_s=30)
        assert failed.result.failed_tests == [node_id]
        target.write_text(copied_source.replace("== 42", ">= 0"))
        passed = await workspace.run_tests(workspace.main, timeout_s=30)
        trace = RunTrace(
            "agent-1",
            (
                RunWitness(1, 1, tuple(failed.result.failed_tests), 1, True),
                Edit(2, path, 2),
                RunWitness(3, 3, tuple(passed.result.failed_tests), 0, True),
            ),
        )
        result = evaluate(trace, (Weakening(kind="loosened", test_id=node_id),))
        assert result.status == "findings" and result.findings[0].edit.path == path
        assert (root / path).read_text() == copied_source
        assert OwnSource("C:/own").pytest_targets == ()
        assert OwnSource("C:/own").pytest_options == ()
    finally:
        workspace.release_runtime()


async def test_self_copy_observed_race(tmp_path, monkeypatch):
    runtime, root, profile, _ = await self_checkout(tmp_path, monkeypatch)
    inventory = await self_inventory(profile, runtime)
    destination = tmp_path / "copy"
    opened = Path.open
    fired = []

    def racing_open(path, mode="r", *args, **kwargs):
        if mode == "xb" and path.is_relative_to(destination) and not fired:
            fired.append(True)
            (root / "demo-repo/shop/value.py").write_text("observed copy race")
        return opened(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    with pytest.raises(ValueError, match=SELF_INVENTORY_REFUSAL):
        copy_template(root, destination, inventory=inventory)
    assert fired


async def test_self_recorded_pin_inventory(tmp_path):
    root = OVERSIGHT_SELF.checkout(tmp_path / ".runtime")
    root.parent.mkdir(parents=True)
    await git(root.parent, "clone", "--no-hardlinks", "--no-checkout", str(REPOSITORY), str(root))
    await git(root, "checkout", "--detach", OVERSIGHT_SELF.commit)
    inventory = await self_inventory(OVERSIGHT_SELF, tmp_path / ".runtime")
    assert inventory.commit == "c08eca088bfa790c8273fe6e41c4e0f2e5f94902"
    assert any(
        e.path.relative_to(root).as_posix() == "demo-repo/pyproject.toml" for e in inventory.entries
    )
    inventory.verify()


async def test_self_inventory_stale_git_and_untracked_refuse(tmp_path, monkeypatch):
    runtime, root, profile, _ = await self_checkout(tmp_path, monkeypatch)
    inventory = await self_inventory(profile, runtime)
    (root / "untracked.py").write_text("not approved")
    with pytest.raises(ValueError, match=SELF_PIN_REFUSAL):
        await self_inventory(profile, runtime)
    with pytest.raises(ValueError, match=SELF_INVENTORY_REFUSAL):
        inventory.verify()
    (root / "untracked.py").unlink()
    inventory = await self_inventory(profile, runtime)
    (root / ".git/HEAD").write_text(profile.commit + "\n")
    with pytest.raises(ValueError, match=SELF_INVENTORY_REFUSAL):
        inventory.verify()
