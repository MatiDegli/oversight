"""Proof 2 infrastructure uses only tiny synthetic packages, without .runtime."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

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
        if task.kind == "feature":
            assert task.hidden_checks and task.reference
        for check in task.hidden_checks:
            text = r.resource(check).read_text()
            assert text not in task.prompt and "def test_" not in task.prompt
        assert "check_p" not in task.prompt and ".patch" not in task.prompt
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
