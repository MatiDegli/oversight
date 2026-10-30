"""Free measurement uses real scans and reference data, with no server, model or network."""

import argparse
import difflib
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
    assert mapped.changed_lines == {"a.py": [1, 7]}
    assert (tmp_path / "a.py").read_text() == before


def test_insertion_inside_a_function_maps_but_added_definitions_and_context_do_not(tmp_path: Path):
    before = "def f():\n    return 1\n\ndef g():\n    return 2\n"
    graph, snapshot = scan(tmp_path, {"a.py": before})
    for after, expected in (
        (before.replace("    return 1", "    value = 1\n    return 1"), ("a:f",)),
        (before.replace("\ndef g", "\ndef new():\n    pass\n\ndef g"), ()),
        (before + "\ndef new():\n    pass\n", ()),
    ):
        patch = "".join(
            difflib.unified_diff(
                before.splitlines(True), after.splitlines(True), fromfile="a/a.py", tofile="b/a.py"
            )
        )
        assert (
            measure.map_targets(graph, snapshot, measure.patch_changes(tmp_path, patch)).targets
            == expected
        )


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
    assert report["nominal_task_pairs"] == 28 and report["excluded_task_pairs"] == 0
    assert all(t["changed_lines"] for t in report["targets"])
    for policy in report["policies"].values():
        assert policy["complete"]
        for variant in policy["variants"].values():
            assert variant["tasks"]["denominator"] == 28
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
    assert report["excluded_task_pairs"] == 2
    assert report["targets"][-1]["excluded"]
    for policy in report["policies"].values():
        for variant in policy["variants"].values():
            assert variant["tasks"]["split"] == variant["tasks"]["denominator"] == 1


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
        assert report["policies"]["count_unimportable"]["variants"][variant]["tasks"]["split"] == 0
        assert report["policies"]["ignore_unimportable"]["variants"][variant]["tasks"]["split"] == 1


@pytest.mark.anyio
async def test_cli_writes_json_and_markdown_without_network(tmp_path: Path, monkeypatch, capsys):
    report = synthetic_report(tmp_path, monkeypatch)
    calls = []

    async def fake_git(root, *args):
        calls.append(args)
        return "" if args[0] == "status" else measure.BOLTONS.commit

    monkeypatch.setattr(measure, "_git", fake_git)
    monkeypatch.setattr(measure, "measure_repository", lambda *args, **kwargs: report)
    output = tmp_path / "output"
    await measure.run(
        argparse.Namespace(boltons_root=tmp_path, demo_root=tmp_path, output_dir=output)
    )
    saved = json.loads((output / "report.json").read_text())
    assert saved["seed"] == measure.SEED
    assert saved["suggested_rule"]["passes"] == {
        "count_unimportable": True,
        "ignore_unimportable": True,
    }
    assert (output / "summary.md").read_text() == capsys.readouterr().out.rstrip() + "\n"
    assert all(call[0] in ("rev-parse", "status") for call in calls)


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
    assert report["targets"][0]["excluded"]
    assert report["policies"]["ignore_unimportable"]["gap_files"] == ["a.py"]
    assert report["policies"]["ignore_unimportable"]["variants"]["call-only"]["tasks"]["split"] == 0


def test_suggested_rule_uses_task_pairs_and_the_inclusive_one_in_five_boundary():
    def repository(split, denominator):
        return {
            "policies": {
                policy: {
                    "variants": {
                        "call-plus-import": {
                            "tasks": {"split": split, "denominator": denominator},
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
        assert all(passed == expected for passed in rule["passes"].values())
        assert rule["label"] == "suggested rule, pending the maintainer's approval"
