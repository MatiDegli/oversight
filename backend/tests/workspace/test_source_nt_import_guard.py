"""Private child guard protocol and real copied-import proofs on Linux."""

import importlib
import importlib.machinery
import importlib.util
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from oversight.workspace import source_nt as nt
from oversight.workspace.runner import run_process
from oversight.workspace.suite import SuiteUnavailable, run_suite

from .test_source_nt_copy import git
from .test_source_nt_imports import mapped_environment

PLUGIN = Path(__file__).parents[2] / "src/oversight/workspace/report_plugin"


def guarded(tmp_path, monkeypatch):
    source, tree, io, _ = mapped_environment(tmp_path)
    own = nt.prepare_environment(source, tree, io.deadline, io=io)
    monkeypatch.syspath_prepend(str(PLUGIN))
    monkeypatch.syspath_prepend(str(tree / "backend/src"))
    plugin = importlib.import_module("oversight_report")
    monkeypatch.setenv("OVERSIGHT_IMPORT_GUARD", own.guard)
    monkeypatch.setenv("OVERSIGHT_TRUSTED_PLUGIN", str(PLUGIN / "oversight_report.py"))
    report = tmp_path / "report.jsonl"
    monkeypatch.setenv("OVERSIGHT_REPORT", str(report))
    return source, tree, plugin, report


@pytest.mark.parametrize(
    "hook", ["sessionstart", "collection_finish", "runtest_logreport", "sessionfinish"]
)
@pytest.mark.parametrize("location", ["file", "origin", "namespace", "spec_namespace"])
def test_original_origins_refused_at_every_hook(tmp_path, monkeypatch, hook, location):
    source, _, plugin, report = guarded(tmp_path, monkeypatch)
    module = ModuleType("rogue")
    module.__spec__ = importlib.machinery.ModuleSpec("rogue", None)
    path = str(source.root / "backend/src")
    if location == "file":
        module.__file__ = path + "/rogue.py"
    elif location == "origin":
        module.__spec__.origin = path + "/rogue.py"
    elif location == "spec_namespace":
        module.__spec__.submodule_search_locations = [path]
    else:
        module.__path__ = [path]
    monkeypatch.setitem(sys.modules, "rogue", module)
    args = (SimpleNamespace(nodeid="t", when="call", outcome="passed"),)
    with pytest.raises(pytest.exit.Exception):
        getattr(plugin, "pytest_" + hook)(
            *args, **({"exitstatus": 0} if hook == "sessionfinish" else {})
        )
    assert json.loads(report.read_text()) == {
        "kind": "guard",
        "ok": False,
        "reason": "loaded module reaches original project",
    }


def test_success_order_late_failure_and_allowed_dependencies(tmp_path, monkeypatch):
    source, tree, plugin, report = guarded(tmp_path, monkeypatch)
    related = ModuleType("__editable__related")
    related.__dict__.update(
        NAMESPACES={"ns": [str(source.root / "lib/ns")]},
        PATH_PLACEHOLDER="__editable__related.__path_hook__",
    )
    monkeypatch.setitem(sys.modules, "__editable__related", related)
    for root in (source.root, tree):
        (root / "lib/ns").mkdir(parents=True)
    monkeypatch.syspath_prepend(str(tree / "lib"))
    monkeypatch.syspath_prepend(str(source.root / "lib"))
    monkeypatch.syspath_prepend(related.__dict__["PATH_PLACEHOLDER"])
    plugin.prepare_imports()
    assert related.__dict__["PATH_PLACEHOLDER"] not in sys.path
    assert str(source.root / "lib") not in sys.path
    spec = importlib.util.find_spec("ns")
    assert spec is not None and list(spec.submodule_search_locations or ()) == [
        str(tree / "lib/ns")
    ]
    module = ModuleType("dependency")
    module.__file__ = str(source.root / ".venv/Lib/site-packages/dependency.py")
    module.__path__ = [str(tmp_path / "unrelated")]
    monkeypatch.setitem(sys.modules, "dependency", module)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, SimpleNamespace()])
    plugin.pytest_collection_finish(None)
    plugin.pytest_runtest_logreport(SimpleNamespace(nodeid="t", when="call", outcome="passed"))
    module.__file__ = str(source.root / "late.py")
    with pytest.raises(pytest.exit.Exception):
        plugin.pytest_sessionfinish(None, 0)
    rows = [json.loads(row) for row in report.read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["guard", "test", "guard"]
    assert rows[0]["ok"] is True and rows[-1]["ok"] is False
    assert plugin._within(str(source.root).upper(), str(source.root / "x.py"))
    assert not plugin._within(str(source.root), str(source.root) + "-sibling/x.py")
    policy = json.loads(os.environ["OVERSIGHT_IMPORT_GUARD"])
    policy["tree"] = str(tree.parent / "other")
    monkeypatch.setenv("OVERSIGHT_IMPORT_GUARD", json.dumps(policy))
    assert plugin.import_problem() == "mapped package outside tested tree"
    monkeypatch.setenv("OVERSIGHT_TRUSTED_PLUGIN", str(tmp_path / "shadow.py"))
    assert plugin.import_problem() == "trusted report plugin shadowed"


@pytest.mark.anyio
@pytest.mark.parametrize("conftest", [False, True])
async def test_probe_and_conftest_path_insertion_are_refused(tmp_path, conftest):
    source, tree, io, _ = mapped_environment(tmp_path)
    if conftest:
        (tree / "conftest.py").write_text(
            f"import sys\nsys.path.insert(0, {str(source.root / 'backend/src')!r})\nimport sample\n"
        )
    (tree / "test_x.py").write_text("def test_x(): pass\n")
    (source.runtime / "tmp").mkdir(exist_ok=True)

    if conftest:
        result = await copied_suite(source, tree, io, missing=not conftest)
        assert result.result.outcome == "error" and result.result.passed == 0
    else:
        with pytest.raises(SuiteUnavailable):
            await copied_suite(source, tree, io, missing=not conftest)


@pytest.mark.anyio
async def test_injected_entry_point_autoload(tmp_path):
    source, tree, io, _ = mapped_environment(tmp_path)
    (source.runtime / "tmp").mkdir()
    info = tree / "copy_autoload_probe-1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: copy-autoload-probe\nVersion: 1\n")
    (info / "entry_points.txt").write_text(
        "[pytest11]\noversight_copy_autoload = copy_autoload_probe\n"
    )
    (tree / "copy_autoload_probe.py").write_text(
        "import pytest\n@pytest.fixture\ndef autoload_value(): return 41\n"
    )
    (tree / "test_autoload.py").write_text(
        "def test_autoload(autoload_value): assert autoload_value == 41\n"
    )
    result = await copied_suite(source, tree, io)
    assert result.result.outcome == "passed", result.output
    assert result.result.passed == 1


@pytest.mark.anyio
async def test_agent_combined_edits_import_copies_and_preserve_original(tmp_path):
    source, main, io, _ = mapped_environment(tmp_path)
    await git(source.root, "init", "-q", "--template=")
    await git(source.root, "add", "backend")
    await git(source.root, "commit", "-qm", "Original")
    before = {p: p.read_bytes() for p in source.root.rglob("*") if p.is_file()}
    (source.runtime / "tmp").mkdir()
    for tree, value in (
        (main, 1),
        (source.runtime / "worktrees/agent-1", 2),
        (source.runtime / "tmp/combined", 3),
    ):
        package = tree / "backend/src/sample"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(f"value = {value}\n")
        (tree / "test_value.py").write_text(
            "import sample\ndef test_value(): assert sample.value == 1\n"
        )

        result = await copied_suite(source, tree, io)
        assert result.result.outcome == ("passed" if value == 1 else "failed"), result.output
        assert result.result.failed == (0 if value == 1 else 1)
    assert before == {p: p.read_bytes() for p in source.root.rglob("*") if p.is_file()}


def test_guard_caches_roots_and_verified_locations_but_rechecks_specs(tmp_path, monkeypatch):
    source, tree, plugin, _ = guarded(tmp_path, monkeypatch)
    module = ModuleType("dependency")
    location = str(source.root / ".venv/Lib/site-packages/dependency.py")
    module.__file__ = location
    module.__spec__ = importlib.machinery.ModuleSpec("dependency", None, origin=location)
    module.__path__ = [location]
    module.__spec__.submodule_search_locations = [location]
    monkeypatch.setitem(sys.modules, "dependency", module)
    resolved = []
    realpath = os.path.realpath

    def resolve(path):
        resolved.append(path)
        return realpath(path)

    monkeypatch.setattr(plugin.os.path, "realpath", resolve)
    assert plugin.import_problem() is None
    checked = resolved.count(location)
    spec_location = str(tree / "backend/src/sample/__init__.py")
    spec_checks = resolved.count(spec_location)
    assert checked == 1
    assert spec_checks > 0
    for _ in range(3):
        assert plugin.import_problem() is None
    for root in (source.root, tree, source.root / ".venv/Lib"):
        assert resolved.count(str(root)) == 1
    assert resolved.count(location) == checked
    assert resolved.count(spec_location) == spec_checks * 4
    module.__file__ = str(source.root / "late.py")
    assert plugin.import_problem() == "loaded module reaches original project"
    # Cached loaded-module locations must not conceal a changed mapped package spec.
    module.__file__ = location
    monkeypatch.setattr(
        plugin.importlib.util,
        "find_spec",
        lambda _: importlib.machinery.ModuleSpec("sample", None, origin=location),
    )
    assert plugin.import_problem() == "mapped package outside tested tree"


async def copied_suite(source, tree, io, *, missing=False):
    def prepare(root, deadline):
        own = nt.prepare_environment(source, root, deadline, io=io)
        assert own.guard is not None
        policy = json.loads(own.guard)
        if missing:
            policy["packages"] = ["absent"]
        return replace(own, python=Path(sys.executable), guard=json.dumps(policy))

    return await run_suite(
        python=Path("unused"),
        plugin_dir=PLUGIN,
        runtime_root=source.runtime,
        tree_root=tree,
        timeout_s=8,
        redact=lambda s: s,
        spawn=run_process,
        _own_prepare=prepare,
    )
