"""Proof 2 infrastructure uses only tiny synthetic packages, without .runtime."""

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from tools.eval.proof2 import __main__ as cli
from tools.eval.proof2 import runner as r


def test_frozen_tasks_and_no_hidden_prompt_text():
    tasks = r.load_tasks()
    assert [t.id for t in tasks] == [f"p{i:02}" for i in range(1, 17)]
    assert {t.id for t in tasks if t.kind == "feature"} == {
        "p04",
        "p05",
        "p06",
        "p09",
        "p11",
        "p13",
        "p16",
    }
    for i, task in enumerate(tasks):
        assert task.after == (["p05"] if task.id == "p15" else [])
        assert task.pairs == (
            [f"p{j:02}" for j in (i // 2 * 2 + 1, i // 2 * 2 + 2)] if i < 14 else []
        )
        assert task.hidden_checks and task.reference
        assert len(task.mutants) == (3 if task.kind == "tests" else 0)
        for check in task.hidden_checks:
            text = r.resource(check).read_text()
            assert text not in task.prompt and "def test_" not in task.prompt
        assert "check_p" not in task.prompt and ".patch" not in task.prompt
    # Computed from git show 981b1ee:tools/eval/proof2/tasks.json (all 16 prompts).
    prompts = json.dumps([t.prompt for t in tasks], ensure_ascii=False).encode()
    assert (
        hashlib.sha256(prompts).hexdigest()
        == "7dc0c7124047d90efab20815dc52c6802532bb5f5f5a8f65093dda8863ed3c6f"
    )


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    assets, tree = tmp_path / "assets", tmp_path / "source"
    for path in (assets / "checks", assets / "references", tree / "boltons", tree / "tests"):
        path.mkdir(parents=True)
    (tree / "boltons/__init__.py").write_text("")
    (tree / "boltons/strutils.py").write_text("VALUE = 0\n")
    (tree / "tests/test_original.py").write_text(
        "from boltons.strutils import VALUE\ndef test_original(): assert VALUE in (0, 1)\n"
    )
    (assets / "checks/check_p04.py").write_text(
        "from boltons.strutils import VALUE\ndef test_feature(): assert VALUE == 1\n"
    )
    (assets / "references/p04.patch").write_text(
        "--- a/boltons/strutils.py\n+++ b/boltons/strutils.py\n"
        "@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n"
    )
    task = r.Task(
        "p04",
        "feature",
        299,
        "strutils",
        "Synthetic task",
        ["checks/check_p04.py"],
        "references/p04.patch",
        [],
        [],
    )
    (assets / "tasks.json").write_text(json.dumps([vars(task)]))
    monkeypatch.setattr(r, "HERE", assets)
    monkeypatch.setattr(r, "PYTHON", Path(sys.executable))
    return assets, tree


def snapshot(tree):
    return {
        str(p.relative_to(tree)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in tree.rglob("*")
        if p.is_file()
    }


def test_check_copies_and_reports(synthetic):
    assets, tree = synthetic
    check = assets / "checks/check_p04.py"
    check.write_text(
        check.read_text() + "def test_write_copy():\n from pathlib import Path\n"
        " Path('boltons/strutils.py').write_text('changed')\n"
    )
    before = snapshot(tree)
    result = asyncio.run(r.check("p04", tree))
    assert (result["passed"], result["failed"], result["errors"]) == (1, 1, 0)
    assert result["task"] == "p04" and result["tree"] == str(tree)
    assert result["failing_test_ids"] == ["_proof2_checks/check_p04.py::test_feature"]
    assert result["duration"] >= 0 and result["exit_code"] == 1
    assert snapshot(tree) == before
    assert not list(assets.glob(".proof2-*"))


@pytest.mark.parametrize("short_circuit", [True, False])
def test_character_check_accepts_both_length_validation_orders(synthetic, short_circuit):
    assets, tree = synthetic
    frozen = Path(r.__file__).with_name("checks") / "check_p04.py"
    (assets / "checks/check_p04.py").write_text(frozen.read_text())
    (tree / "boltons/strutils.py").write_text(
        f"SHORT_CIRCUIT = {short_circuit!r}\n"
        + dedent("""\
        def ellipsize(text, max_len=160, *, ellipsis="…", by="word"):
            if by not in ("word", "char"):
                raise ValueError("invalid mode")
            if SHORT_CIRCUIT and by == "char" and len(text) <= max_len:
                return text
            if max_len <= len(ellipsis):
                raise ValueError("invalid length")
            if len(text) <= max_len:
                return text
            limit = max_len - len(ellipsis)
            if by == "char":
                return text[:limit] + ellipsis
            cut = text.rfind(" ", 0, limit + 1)
            if cut <= 0:
                cut = limit
            return text[:cut].rstrip(" .,;:!?") + ellipsis
        """)
    )
    result = asyncio.run(r.check("p04", tree))
    assert result["exit_code"] == 0, result
    assert result["passed"] > 0


def test_range_check_accepts_unpack_type_error(synthetic):
    _, tree = synthetic
    frozen = Path(r.__file__).with_name("checks") / "check_p05.py"
    hidden = tree / "_proof2_checks"
    hidden.mkdir()
    (hidden / frozen.name).write_text(frozen.read_text())
    (tree / "boltons/mathutils.py").write_text(
        dedent("""\
        def range_union(ranges):
            for pair in ranges:
                start, end = pair
                try:
                    invalid = start > end
                except TypeError as exc:
                    raise ValueError("invalid endpoints") from exc
                if invalid:
                    raise ValueError("reversed range")

        range_intersection = range_union
        """)
    )
    result = asyncio.run(
        r.suite(tree, ["_proof2_checks/check_p05.py::test_invalid_range_is_not_skipped"])
    )
    assert result["exit_code"] == 0, result
    assert result["passed"] > 0


@pytest.mark.parametrize("problem", ["unknown", "no_checks", "missing", "not_boltons", "symlink"])
def test_refusals(synthetic, problem):
    assets, tree = synthetic
    task = "unknown" if problem == "unknown" else "p04"
    if problem == "no_checks":
        tasks = json.loads((assets / "tasks.json").read_text())
        tasks[0]["hidden_checks"] = []
        (assets / "tasks.json").write_text(json.dumps(tasks))
    if problem == "missing":
        tree = tree / "absent"
    if problem == "not_boltons":
        (tree / "boltons/__init__.py").unlink()
    if problem == "symlink":
        (tree / "external").symlink_to(assets, target_is_directory=True)
    with pytest.raises(ValueError):
        asyncio.run(r.check(task, tree))


@pytest.mark.parametrize("condition", ["all_pass", "base", "reference", "original", "collection"])
def test_validate_conditions(synthetic, condition, capsys):
    assets, tree = synthetic
    hidden = assets / "checks/check_p04.py"
    if condition in ("base", "reference"):
        hidden.write_text(
            hidden.read_text().replace("== 1", ">= 0" if condition == "base" else "== 2")
        )
    if condition == "original":
        original = tree / "tests/test_original.py"
        original.write_text(original.read_text().replace("in (0, 1)", "== 0"))
    if condition == "collection":
        hidden.write_text("import missing_proof2_dependency\n")
    before = snapshot(tree)
    rows = asyncio.run(r.validate(tree))
    assert rows[0]["ok"] == (condition == "all_pass")
    for name, failing in [
        ("base_fails", "base"),
        ("reference_passes", "reference"),
        ("original_passes", "original"),
    ]:
        assert rows[0][name] == (
            condition != failing and (condition != "collection" or name == "original_passes")
        )
    assert json.loads(capsys.readouterr().out)["task"] == "p04"
    assert snapshot(tree) == before


def test_timeout_is_an_error(synthetic):
    assets, tree = synthetic
    (assets / "checks/check_p04.py").write_text("import time\ndef test_wait(): time.sleep(30)\n")
    result = asyncio.run(r.check("p04", tree, timeout_s=0.15))
    assert result["exit_code"] == 124 and result["errors"] == 1
    assert not list(assets.glob(".proof2-*"))


@pytest.mark.parametrize("command", ["check", "validate"])
def test_cli_exit_and_json(synthetic, monkeypatch, capsys, command):
    _, tree = synthetic
    args = (
        ["proof2", "check", "--task", "p04", "--tree", str(tree)]
        if command == "check"
        else ["proof2", "validate", "--source", str(tree)]
    )
    monkeypatch.setattr(sys, "argv", args)
    assert cli.main() == (1 if command == "check" else 0)
    assert json.loads(capsys.readouterr().out)["task"] == "p04"


def test_cli_refusal_json(synthetic, monkeypatch, capsys):
    _, tree = synthetic
    monkeypatch.setattr(sys, "argv", ["proof2", "check", "--task", "p00", "--tree", str(tree)])
    assert cli.main() == 2
    assert "unknown task" in json.loads(capsys.readouterr().out)["error"]


@pytest.fixture
def d3(synthetic):
    assets, tree = synthetic
    base = tree.parent / "base"
    shutil.copytree(tree, base)
    reference = assets / "references/p10_test.py"
    reference.write_text(
        "from boltons.strutils import VALUE\ndef test_value(): assert VALUE == 0\n"
    )
    shutil.copyfile(reference, tree / "tests/test_added.py")
    mutants = []
    for i in range(1, 4):
        name = f"mutants/p10/m{i}.py"
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# targets: VALUE\nVALUE = {i}\n")
        mutants.append(name)
    task = r.Task(
        "p10",
        "tests",
        None,
        "strutils",
        "Synthetic D3",
        ["references/p10_test.py"],
        "references/p10_test.py",
        [],
        [],
        mutants,
    )
    (assets / "tasks.json").write_text(json.dumps([vars(task)]))
    return assets, tree, base


@pytest.mark.parametrize(
    "condition", ["production", "no_tests", "failing", "one_kill", "pass", "two_kills"]
)
def test_d3_conditions_and_immutable_inputs(d3, condition):
    assets, tree, base = d3
    added = tree / "tests/test_added.py"
    if condition == "production":
        (tree / "README").write_text("production artifact changed")
    elif condition == "no_tests":
        added.unlink()
    elif condition == "failing":
        added.write_text(added.read_text().replace("== 0", "== 9"))
    elif condition in ("one_kill", "two_kills"):
        added.write_text(
            added.read_text().replace("== 0", "!= 1" if condition == "one_kill" else "< 2")
        )
    before_tree, before_base = snapshot(tree), snapshot(base)
    result = asyncio.run(r.check("p10", tree, base=base))
    assert result["exit_code"] == (0 if condition in ("pass", "two_kills") else 1)
    assert result["production_unchanged"] == (condition != "production")
    assert result["new_tests_present"] == (condition != "no_tests")
    if condition == "production":
        assert result["reason"] == "production code changed"
        assert result["production_changed_paths"] == ["README"]
    if condition == "failing":
        assert not result["new_tests_pass"] and result["tests"]["failed"] == 1
    if condition in ("pass", "one_kill", "two_kills"):
        assert result["new_tests_pass"]
        expected = {"pass": ["m1", "m2", "m3"], "one_kill": ["m1"], "two_kills": ["m2", "m3"]}
        assert result["killed_mutants"] == expected[condition]
        assert result["mutation_threshold"] == (condition != "one_kill")
    assert snapshot(tree) == before_tree and snapshot(base) == before_base
    assert not list(assets.glob(".proof2-*"))


def test_missing_mutant_target_is_refused_before_append(d3):
    assets, tree, base = d3
    mutant = assets / "mutants/p10/m1.py"
    mutant.write_text("# targets: VALUE, Missing.method\nVALUE = 1\n")
    before = snapshot(tree)
    with pytest.raises(ValueError, match=r"target unavailable.*Missing\.method"):
        asyncio.run(r.check("p10", tree, base=base))
    assert snapshot(tree) == before
    task = r.load_tasks()[0]
    with pytest.raises(ValueError, match="target unavailable"):
        asyncio.run(r.apply_mutant(tree, task, task.mutants[0]))
    assert snapshot(tree) == before


def test_d3_requires_base(d3):
    _, tree, _ = d3
    with pytest.raises(ValueError, match="--base is required"):
        asyncio.run(r.check("p10", tree))


@pytest.mark.parametrize("kind", ["bug", "feature"])
def test_cli_refuses_base_for_other_kinds(synthetic, monkeypatch, capsys, kind):
    assets, tree = synthetic
    tasks = json.loads((assets / "tasks.json").read_text())
    tasks[0]["kind"] = kind
    (assets / "tasks.json").write_text(json.dumps(tasks))
    monkeypatch.setattr(
        sys, "argv", ["proof2", "check", "--task", "p04", "--tree", str(tree), "--base", str(tree)]
    )
    assert cli.main() == 2
    assert "--base is only allowed" in json.loads(capsys.readouterr().out)["error"]


def test_validate_d3_reference_and_informational_original(d3, capsys):
    _, _, base = d3
    before = snapshot(base)
    result = asyncio.run(r.validate(base))[0]
    assert result["ok"] and result["all_mutants_apply"] and result["reference_passes"]
    assert result["reference_kills"] == ["m1", "m2", "m3"]
    assert result["original_kills"] == ["m2", "m3"]
    assert json.loads(capsys.readouterr().out)["task"] == "p10"
    assert snapshot(base) == before


@pytest.mark.parametrize("path", [".proof2-artifact", ".venv/artifact", "__pycache__/artifact"])
def test_d3_compares_even_artifacts_excluded_from_execution_copy(d3, path):
    _, tree, base = d3
    changed = tree / path
    changed.parent.mkdir(exist_ok=True)
    changed.write_bytes(b"changed")
    result = asyncio.run(r.check("p10", tree, base=base))
    assert result["reason"] == "production code changed"
    assert result["production_changed_paths"] == [path]


def test_d3_environment_files_refused_without_reading(d3, monkeypatch):
    _, tree, base = d3
    environment = tree / ".env.example"
    environment.touch()
    read = Path.read_bytes

    def guarded(path):
        assert path != environment
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    with pytest.raises(ValueError, match="environment files cannot be compared"):
        asyncio.run(r.check("p10", tree, base=base))
