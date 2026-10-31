"""Free measurement uses real scans and reference data, with no server, model or network."""

import argparse
import difflib
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import split_measure as measure
from oversight.graph import WorkspaceGraph
from oversight_contracts.graph import GraphEdge

COMMIT = "a" * 40


def scan(root: Path, files: dict[str, str]):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    graph = WorkspaceGraph(root)
    return graph, graph.scan(COMMIT)


def task(node: str) -> measure.Task:
    return measure.Task(node, (node,), {}, "synthetic")


def evaluate(snapshot, a="a:f", b="b:g", *, imports=None, complete=True, variant="call-only"):
    return measure.evaluate_pair(
        snapshot, (task(a), task(b)), complete=complete, imports=imports or set(), variant=variant
    )


def test_calls_split_but_imports_couple_and_fallback_resolves_relative_imports(tmp_path: Path):
    _, snapshot = scan(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from . import b\ndef f(): return 1\n",
            "pkg/b.py": "def g(): return 2\n",
        },
    )
    links = measure.import_links(tmp_path, snapshot)
    assert ("pkg/a.py", "pkg/b.py") in links
    assert evaluate(snapshot, "pkg.a:f", "pkg.b:g", imports=links)["verdict"] == "split"
    refusal = evaluate(snapshot, "pkg.a:f", "pkg.b:g", imports=links, variant="call-plus-import")
    assert refusal["diagnostics"] == [{"code": "import_coupling", "ids": ["pkg/a.py", "pkg/b.py"]}]
    without_imports = snapshot.model_copy(update={"edges": []})
    assert ("pkg/a.py", "pkg/b.py") in measure.import_links(tmp_path, without_imports)


@pytest.mark.parametrize("confidence,reason", [("exact", None), ("possible", "unknown_receiver")])
def test_calls_including_unresolved_possible_calls_refuse(tmp_path: Path, confidence, reason):
    _, snapshot = scan(tmp_path, {"a.py": "def f(): pass\n", "b.py": "def g(): pass\n"})
    edge = GraphEdge(
        id="synthetic", kind="calls", src="a:f", dst="b:g", confidence=confidence, reason=reason
    )
    snapshot = snapshot.model_copy(update={"edges": [edge]})
    assert evaluate(snapshot)["diagnostics"][0]["code"] == "coupled_targets"


def test_task_targets_must_stay_together(tmp_path: Path):
    _, snapshot = scan(tmp_path, {f"{n}.py": "def f(): pass\n" for n in "abcd"})
    pair = (
        replace(task("task-1"), targets=("a:f", "c:f")),
        replace(task("task-2"), targets=("b:f", "d:f")),
    )
    refusal = measure.evaluate_pair(
        snapshot, pair, complete=True, imports=set(), variant="call-only"
    )
    assert refusal["diagnostics"][0]["code"] == "task_targets_mixed"


def test_patch_targets_use_base_spans_methods_and_decorators(tmp_path: Path):
    before = "@wrap\ndef f():\n    return 1\n\nclass C:\n    def m(self):\n        return 2\n"
    after = before.replace("@wrap", "@other").replace("return 2", "return 3")
    after += "\ndef added():\n    return 4\n"
    graph, snapshot = scan(tmp_path, {"a.py": before})
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(True), after.splitlines(True), fromfile="a/a.py", tofile="b/a.py"
        )
    )
    changes = measure.patch_changes(tmp_path, patch)
    mapped = measure.map_targets(graph, snapshot, changes)
    assert mapped.targets == ("a:C.m", "a:f")
    assert mapped.mapping_kind == "changed lines"
    assert mapped.changed_lines == {"a.py": [1, 7]}
    assert (tmp_path / "a.py").read_text() == before


def test_insertions_keep_primary_targets_and_label_added_definition_fallbacks(tmp_path: Path):
    before = "def f():\n    return 1\n\ndef g():\n    return 2\n"
    graph, snapshot = scan(tmp_path, {"a.py": before})
    for after, expected, kind in (
        (
            before.replace("    return 1", "    value = 1\n    return 1"),
            ("a:f",),
            "enclosing insertion",
        ),
        (
            before.replace("\ndef g", "\ndef new():\n    pass\n\ndef g"),
            ("a",),
            "enclosing fallback",
        ),
        (before + "\ndef new():\n    pass\n", ("a",), "enclosing fallback"),
    ):
        patch = "".join(
            difflib.unified_diff(
                before.splitlines(True), after.splitlines(True), fromfile="a/a.py", tofile="b/a.py"
            )
        )
        mapped = measure.map_targets(graph, snapshot, measure.patch_changes(tmp_path, patch))
        assert mapped.targets == expected
        assert mapped.mapping_kind == kind


def test_bad_reference_context_fails_closed_and_paths_cannot_escape(tmp_path: Path):
    scan(tmp_path, {"a.py": "def f(): pass\n"})
    with pytest.raises(ValueError, match="context mismatch"):
        measure.patch_changes(tmp_path, "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-wrong\n+right\n")
    with pytest.raises(ValueError, match="unsafe input path"):
        measure.patch_changes(tmp_path, "--- a/../a.py\n+++ b/../a.py\n@@ -1 +1 @@\n-x\n+y\n")


def test_seeded_sample_is_unique_deterministic_and_excludes_tests(tmp_path: Path):
    _, snapshot = scan(
        tmp_path,
        {
            "a.py": "\n".join(f"def f{i}(): pass" for i in range(50)),
            "tests/test_a.py": "def test_a(): pass\n",
        },
    )
    first = measure.sample_pairs(snapshot)
    assert len(first) == 1000
    assert first == measure.sample_pairs(
        snapshot.model_copy(update={"nodes": list(reversed(snapshot.nodes))})
    )
    assert len({(a.id, b.id) for a, b in first}) == 1000
    assert all(a.id < b.id and "test" not in a.id + b.id for a, b in first)
    assert len(measure.sample_pairs(snapshot, 2000)) == 1225


def test_demo_reference_targets_and_28_pairs_are_auditable():
    root = measure.REPO / "demo-repo"
    report = measure.measure_repository(
        root, COMMIT, measure.REPO / "tools/eval/tasks.json", sample=False
    )
    targets = {t["id"]: t["targets"] for t in report["targets"]}
    assert targets == {
        **{f"pricing-0{i}": ("shop.pricing:compute_price",) for i in range(1, 6)},
        "pricing-06": ("shop.pricing:coupon_percent",),
        "inventory-07": ("shop.inventory:Inventory.reserve",),
        "inventory-08": ("shop.inventory:Inventory.reserve",),
    }
    assert report["nominal_task_pairs"] == 28
    assert report["excluded_task_pairs"] == {m: 0 for m in measure.TASK_MAPPINGS}
    assert all(t["changed_lines"] for t in report["targets"])
    for policy in report["policies"].values():
        assert policy["complete"]
        for variant in policy["variants"].values():
            assert variant["tasks_without_fallback"]["denominator"] == 28
            assert variant["tasks_with_fallback"]["denominator"] == 28
    summary = measure.markdown(measure.build_report(report, report, COMMIT))
    assert "pending the maintainer's approval" in summary
    assert "shop.inventory:Inventory.reserve" in summary
    assert "28" in summary
    assert report["reference_hashes"]


def synthetic_report(tmp_path: Path, monkeypatch, *, gap="", unimportable=False):
    data = tmp_path / "data"
    data.mkdir()
    root = tmp_path / "source"
    files = {"a.py": "def f(): return 1\n", "b.py": "def g(): return 2\n"}
    if gap:
        files["broken.py"] = gap
    if unimportable:
        files["bad-name.py"] = ""
    scan(root, files)
    tasks = [
        {"id": "a", "reference_solution": "a.json"},
        {"id": "b", "reference_solution": "b.json"},
        {"id": "empty", "reference": "new_test.py"},
    ]
    (data / "tasks.json").write_text(json.dumps(tasks))
    for name, old in (("a", "1"), ("b", "2")):
        (data / f"{name}.json").write_text(
            json.dumps(
                [
                    {
                        "path": f"{name}.py",
                        "old": f"return {old}",
                        "new": "return 3",
                    }
                ]
            )
        )
    (data / "new_test.py").write_text("def test_added(): pass\n")
    monkeypatch.setattr(measure, "REPO", tmp_path)
    return measure.measure_repository(root, COMMIT, data / "tasks.json", sample=True)


def test_no_target_task_is_reported_and_excluded(tmp_path: Path, monkeypatch):
    report = synthetic_report(tmp_path, monkeypatch)
    assert report["nominal_task_pairs"] == 3
    assert report["excluded_task_pairs"] == {m: 2 for m in measure.TASK_MAPPINGS}
    assert report["targets"][-1]["excluded_without_fallback"]
    assert report["targets"][-1]["mapping_kind"] == "none"
    for policy in report["policies"].values():
        for variant in policy["variants"].values():
            assert (
                variant["tasks_without_fallback"]["split"]
                == variant["tasks_without_fallback"]["denominator"]
                == 1
            )
            assert (
                variant["tasks_with_fallback"]["split"]
                == variant["tasks_with_fallback"]["denominator"]
                == 1
            )


def test_incomplete_tree_never_splits(tmp_path: Path, monkeypatch):
    report = synthetic_report(tmp_path, monkeypatch, gap="def broken(:\n")
    for policy in report["policies"].values():
        assert not policy["complete"] and policy["gap_files"] == ["broken.py"]
        for variant in policy["variants"].values():
            for group in variant.values():
                assert group["split"] == 0 and group["denominator"] == 1
                assert group["refusals"]["incomplete_evidence"] == 1
                assert group["pairs"][0]["verdict"] == "no split possible"


def test_unimportable_paths_have_two_explicit_verdicts(tmp_path: Path, monkeypatch):
    report = synthetic_report(tmp_path, monkeypatch, unimportable=True)
    assert report["unimportable_files"] == ["bad-name.py"]
    assert not report["policies"]["count_unimportable"]["complete"]
    assert report["policies"]["ignore_unimportable"]["complete"]
    for variant in ("call-only", "call-plus-import"):
        assert (
            report["policies"]["count_unimportable"]["variants"][variant]["tasks_without_fallback"][
                "split"
            ]
            == 0
        )
        assert (
            report["policies"]["ignore_unimportable"]["variants"][variant][
                "tasks_without_fallback"
            ]["split"]
            == 1
        )


@pytest.mark.anyio
async def test_cli_writes_json_and_markdown_without_network(tmp_path: Path, monkeypatch, capsys):
    report = synthetic_report(tmp_path, monkeypatch)
    calls = []

    async def fake_git(root, *args):
        calls.append((root, args))
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args[0] == "ls-files":
            return "a.py\0b.py\0"
        return "" if args[0] == "status" else measure.BOLTONS.commit

    monkeypatch.setattr(measure, "_git", fake_git)
    monkeypatch.setattr(
        measure, "measure_repository", lambda *args, **kwargs: json.loads(json.dumps(report))
    )
    output = tmp_path / "output"
    await measure.run(
        argparse.Namespace(boltons_root=tmp_path, demo_root=tmp_path / "source", output_dir=output)
    )
    saved = json.loads((output / "report.json").read_text())
    assert saved["seed"] == measure.SEED
    assert saved["suggested_rule"]["passes"] == {
        "count_unimportable": {m: True for m in measure.TASK_MAPPINGS},
        "ignore_unimportable": {m: True for m in measure.TASK_MAPPINGS},
    }
    assert (output / "summary.md").read_text() == capsys.readouterr().out.rstrip() + "\n"
    assert all(args[0] in ("rev-parse", "status", "ls-files") for _, args in calls)
    demo = saved["repositories"]["demo"]
    assert "commit" not in demo
    assert demo["parent_commit"] == measure.BOLTONS.commit
    assert demo["input_identity"] == f"source/ at {measure.BOLTONS.commit}"
    assert "content" in demo["content_hash_label"].lower()
    assert demo["content_sha256"] in (output / "summary.md").read_text()
    assert (tmp_path, ("status", "--porcelain", "--untracked-files=all")) in calls


@pytest.mark.anyio
@pytest.mark.parametrize("dirty", [False, True])
async def test_cli_refuses_wrong_pin_or_dirty_input_before_scanning(
    tmp_path: Path, monkeypatch, dirty
):
    async def fake_git(root, *args):
        return measure.BOLTONS.commit if dirty and args[0] == "rev-parse" else "wrong"

    def never_scan(*args, **kwargs):
        pytest.fail("an unverified checkout must not be measured")

    monkeypatch.setattr(measure, "_git", fake_git)
    monkeypatch.setattr(measure, "measure_repository", never_scan)
    with pytest.raises(ValueError, match="clean checkout"):
        await measure.run(argparse.Namespace(boltons_root=tmp_path))


def test_changed_scan_evidence_fails_closed(tmp_path: Path, monkeypatch):
    scan(tmp_path, {"a.py": "def f(): pass\n"})
    completeness = measure.graph_completeness(tmp_path)
    monkeypatch.setattr(
        measure,
        "graph_completeness",
        lambda root: tuple(replace(f, sha256="0" * 64) for f in completeness),
    )
    with pytest.raises(ValueError, match="source changed"):
        measure.measure_repository(tmp_path, COMMIT, tmp_path / "unused.json", sample=False)


@pytest.mark.parametrize("contents", [b"def f(:\n", b"x = '\xff'\n"])
def test_unparsed_reference_file_is_reported_without_trying_to_map_its_lines(
    tmp_path: Path, monkeypatch, contents
):
    report = synthetic_report(tmp_path, monkeypatch)
    source = tmp_path / "source"
    (source / "a.py").write_bytes(contents)
    report = measure.measure_repository(source, COMMIT, tmp_path / "data/tasks.json", sample=False)
    assert report["targets"][0]["excluded_without_fallback"]
    assert report["policies"]["ignore_unimportable"]["gap_files"] == ["a.py"]
    assert (
        report["policies"]["ignore_unimportable"]["variants"]["call-only"][
            "tasks_without_fallback"
        ]["split"]
        == 0
    )


def test_suggested_rule_uses_task_pairs_and_the_inclusive_one_in_five_boundary():
    def repository(split, denominator):
        return {
            "policies": {
                policy: {
                    "variants": {
                        "call-plus-import": {
                            **{
                                f"tasks_{m}": {"split": split, "denominator": denominator}
                                for m in measure.TASK_MAPPINGS
                            },
                            "sample": {"split": 1000, "denominator": 1000},
                        }
                    }
                }
                for policy in ("count_unimportable", "ignore_unimportable")
            }
        }

    for b, d, expected in (
        (repository(1, 5), repository(1, 28), True),
        (repository(1, 6), repository(1, 28), False),
        (repository(1, 5), repository(0, 28), False),
        (repository(0, 0), repository(1, 28), False),
    ):
        rule = measure.build_report(b, d, COMMIT)["suggested_rule"]
        assert all(
            passed == expected for policy in rule["passes"].values() for passed in policy.values()
        )
        assert rule["label"] == "suggested rule, pending the maintainer's approval"


@pytest.mark.parametrize(
    "last_member", ["    value = 1\n", "    def existing(self):\n        return 1\n"]
)
def test_added_class_member_and_module_function_use_fallback_denominators(
    tmp_path: Path, monkeypatch, last_member
):
    report = synthetic_report(tmp_path, monkeypatch)
    root, data = tmp_path / "source", tmp_path / "data"
    additions = {
        "class_addition": (
            "c.py",
            "class C:\n" + last_member,
            "\n    def added(self):\n        return 2\n",
            "c:C",
        ),
        "module_addition": (
            "d.py",
            "def existing():\n    return 1\n",
            "\ndef added():\n    return 2\n",
            "d",
        ),
    }
    tasks = json.loads((data / "tasks.json").read_text())
    for task_id, (path, before, addition, _) in additions.items():
        (root / path).write_text(before)
        patch = "".join(
            difflib.unified_diff(
                before.splitlines(True),
                (before + addition).splitlines(True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        (data / f"{task_id}.patch").write_text(patch)
        tasks.append({"id": task_id, "reference": f"{task_id}.patch"})
    (data / "tasks.json").write_text(json.dumps(tasks))
    report = measure.measure_repository(root, COMMIT, data / "tasks.json", sample=False)
    targets = {t["id"]: t for t in report["targets"]}
    for task_id, (_, _, _, expected) in additions.items():
        assert targets[task_id]["targets"] == (expected,)
        assert targets[task_id]["mapping_kind"] == "enclosing fallback"
        assert targets[task_id]["excluded_without_fallback"]
        assert not targets[task_id]["excluded_with_fallback"]
    assert report["eligible_task_pairs"] == {"without_fallback": 1, "with_fallback": 6}
    assert report["excluded_task_pairs"] == {"without_fallback": 9, "with_fallback": 4}
    for policy in report["policies"].values():
        for variant in policy["variants"].values():
            for mapping, expected in (("without_fallback", 1), ("with_fallback", 6)):
                assert variant[f"tasks_{mapping}"]["denominator"] == expected
                assert variant[f"tasks_{mapping}"]["split"] == expected
    summary = measure.markdown(measure.build_report(report, report, COMMIT))
    assert "100.0% (1 eligible pairs)" in summary
    assert "100.0% (6 eligible pairs)" in summary
    assert "| class_addition | enclosing fallback | c:C | excluded | included |" in summary
    assert "| module_addition | enclosing fallback | d | excluded | included |" in summary


def test_nested_added_definition_falls_back_to_existing_method_and_refusals_stay_refusals(
    tmp_path: Path,
):
    before = "class C:\n    def m(self):\n        return 1\n"
    after = before.replace(
        "        return 1", "        def inner():\n            pass\n        return 1"
    )
    graph, snapshot = scan(tmp_path, {"a.py": before})
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(True), after.splitlines(True), fromfile="a/a.py", tofile="b/a.py"
        )
    )
    mapped = measure.map_targets(graph, snapshot, measure.patch_changes(tmp_path, patch))
    assert mapped.targets == ("a:C.m",) and mapped.mapping_kind == "enclosing fallback"
    refused = evaluate(snapshot, "a:C", "a")
    assert refused["verdict"] == "no split possible"
    assert refused["diagnostics"][0]["code"] == "coupled_targets"


async def committed_demo(parent: Path) -> tuple[Path, str]:
    root = parent / "demo-repo"
    root.mkdir()
    (root / "README.md").write_text("Plain demo files.\n")
    (root / "module.py").write_text("def f(): return 1\n")
    await measure._git(parent, "init", "-q")
    await measure._git(parent, "add", "--", "demo-repo")
    await measure._git(
        parent,
        "-c",
        "user.name=Split test",
        "-c",
        "user.email=split@example.invalid",
        "commit",
        "-qm",
        "Test parent commit",
    )
    return root, await measure._git(parent, "rev-parse", "HEAD")


@pytest.mark.anyio
async def test_demo_identity_labels_parent_commit_and_hashes_all_tracked_input_files(
    tmp_path: Path,
):
    root, commit = await committed_demo(tmp_path)
    identity = await measure.demo_identity(root)
    hashes = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("README.md", "module.py")
    }
    expected = hashlib.sha256(
        "".join(f"{p}\0{h}\n" for p, h in hashes.items()).encode()
    ).hexdigest()
    assert identity["input_identity"] == f"demo-repo/ at {commit}"
    assert identity["parent_commit"] == commit and "commit" not in identity
    assert identity["content_file_hashes"] == hashes
    assert identity["content_sha256"] == expected
    assert identity == await measure.demo_identity(root)
    (root / "README.md").write_text("Changed demo documentation.\n")
    await measure._git(tmp_path, "add", "--", "demo-repo/README.md")
    await measure._git(
        tmp_path,
        "-c",
        "user.name=Split test",
        "-c",
        "user.email=split@example.invalid",
        "commit",
        "-qm",
        "Test changed demo content",
    )
    assert (await measure.demo_identity(root))["content_sha256"] != expected


@pytest.mark.anyio
@pytest.mark.parametrize("dirty_path", ["demo-repo/README.md", "untracked.txt"])
async def test_demo_identity_rejects_dirty_parent_trees(tmp_path: Path, dirty_path):
    root, _ = await committed_demo(tmp_path)
    (tmp_path / dirty_path).write_text("Dirty parent tree.\n")
    with pytest.raises(ValueError, match="clean parent checkout"):
        await measure.demo_identity(root)


@pytest.mark.anyio
async def test_cli_checks_demo_parent_before_measuring_either_input(tmp_path: Path, monkeypatch):
    async def fake_git(root, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args[0] == "status":
            return " M demo-repo/README.md" if root == tmp_path else ""
        return measure.BOLTONS.commit

    def never_measure(*args, **kwargs):
        pytest.fail("dirty demo parent must be rejected before measuring")

    monkeypatch.setattr(measure, "_git", fake_git)
    monkeypatch.setattr(measure, "measure_repository", never_measure)
    with pytest.raises(ValueError, match="clean parent checkout"):
        await measure.run(
            argparse.Namespace(boltons_root=tmp_path / "boltons", demo_root=tmp_path / "demo-repo")
        )
