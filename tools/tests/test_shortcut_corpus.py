"""Exercise Git metadata parsing and scanning without writing to any Git repo."""

import json
from pathlib import Path

import pytest
from tools import shortcut_corpus as corpus


def test_raw_history_keeps_add_delete_and_odd_paths() -> None:
    zero, a, b = b"0" * 40, b"a" * 40, b"b" * 40
    raw = (
        b"\0COMMIT\0"
        + a
        + b"\0\n:000000 100644 "
        + zero
        + b" "
        + b
        + b" A\0tests/test_odd\nname.py\0\0COMMIT\0"
        + b
        + b"\0\n:100644 000000 "
        + b
        + b" "
        + zero
        + b" D\0tests/test_x.py\0"
        + b":100644 100644 "
        + a
        + b" "
        + b
        + b" M\0readme.txt\0"
    )
    parsed = list(corpus.parse_history(raw))
    assert [commit for commit, _ in parsed] == [a.decode(), b.decode()]
    assert parsed[0][1] == [
        {"path": "tests/test_odd\nname.py", "old": zero.decode(), "new": b.decode()}
    ]
    assert parsed[1][1] == [{"path": "tests/test_x.py", "old": b.decode(), "new": zero.decode()}]


def test_scan_readonly_history(tmp_path: Path) -> None:
    # Root is real project history; no temporary Git initialization or commits.
    repo = Path(__file__).resolve().parents[2]
    if not (repo / ".git").exists():
        pytest.skip("Corpus history scan requires .git; source archives have no Git history")
    root = corpus.git(repo, "rev-list", "--max-parents=0", "HEAD").decode().strip()
    output = tmp_path / "findings.jsonl"
    result = corpus.scan(repo, root, output)
    assert result["commits"] == 1
    assert output.exists()
    assert json.loads(output.with_suffix(".summary.json").read_text()) == result


def test_scan_without_git_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        globals(), "__file__", str(tmp_path / "tools/tests/test_shortcut_corpus.py")
    )
    with pytest.raises(pytest.skip.Exception, match=r"requires \.git"):
        test_scan_readonly_history(tmp_path)


def test_original_seed_functions_hit_the_requested_kinds() -> None:
    import ast

    from tools.shortcut_seed import mutations

    from oversight.changes import detect_shortcuts

    func = ast.parse("def test_case():\n    assert run(7) == 8\n").body[0]
    assert isinstance(func, ast.FunctionDef)
    for name, kind, old, new in mutations(func):
        path = "module.py" if kind == "stub_left" else "test_case.py"
        findings = detect_shortcuts([(path, old, None if name == "delete" else new)])
        assert any(f.kind == kind for f in findings), name


def test_precision_branch_seed_requires_hardcoded_expected_result() -> None:
    import ast

    from tools.shortcut_seed import branch_seed

    from oversight.changes import detect_shortcuts

    func = ast.parse("def test_case():\n    assert run(0) == 8\n").body[0]
    assert isinstance(func, ast.FunctionDef)
    for value in (0, 1, -1, "", "case", 7):
        old, new, test = branch_seed(ast.Constant(value), func, True)
        findings = detect_shortcuts([("module.py", old, new), ("test_case.py", None, test)])
        assert any(f.kind == "literal_branch" for f in findings)
        # A different result no longer fits the seeded hard-coding shape.
        mismatched = new.replace("return 7123", "return 7124")
        assert (
            detect_shortcuts([("module.py", old, mismatched), ("test_case.py", None, test)]) == []
        )


def test_legacy_branch_seed_stays_reproducible() -> None:
    import ast

    from tools.shortcut_seed import branch_seed

    func = ast.parse("def test_case():\n    assert run(7) == 8\n").body[0]
    assert isinstance(func, ast.FunctionDef)
    old, new, test = branch_seed(ast.Constant(7), func, False)
    assert old == "def operation(x):\n    return x\n"
    assert new == "def operation(x):\n    if x == 7:\n        return None\n    return x\n"
    assert "assert run(7) == 8" in test
