"""Exercise Git metadata parsing and scanning without writing to any Git repo."""

import json
from pathlib import Path

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
    root = corpus.git(repo, "rev-list", "--max-parents=0", "HEAD").decode().strip()
    output = tmp_path / "findings.jsonl"
    result = corpus.scan(repo, root, output)
    assert result["commits"] == 1
    assert output.exists()
    assert json.loads(output.with_suffix(".summary.json").read_text()) == result


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
