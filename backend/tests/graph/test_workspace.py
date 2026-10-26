"""Scanning a workspace: content hashes, parse errors and deterministic output."""

from pathlib import Path

from graph_helpers import COMMIT, scan, summary, write

from oversight.graph import WorkspaceGraph


def test_a_rescan_parses_only_new_and_changed_files(tmp_path: Path) -> None:
    graph, first = scan(tmp_path, {"a.py": "def f(): ...\n", "b.py": "def g(): ...\n"})
    assert graph.last_parsed == ("a.py", "b.py")
    unchanged = graph.scan(COMMIT)
    assert graph.last_parsed == ()
    assert unchanged == first
    write(tmp_path, {"b.py": "def g(): ...\ndef h(): ...\n", "c.py": ""})
    changed = graph.scan(COMMIT)
    assert graph.last_parsed == ("b.py", "c.py")
    assert changed.version != first.version
    assert "b:h" in {n.id for n in changed.nodes}


def test_a_rescan_still_resolves_unchanged_files_against_changed_ones(tmp_path: Path) -> None:
    graph, _ = scan(
        tmp_path,
        {"lib.py": "def run(): ...\n", "app.py": "from lib import run\ndef go():\n    run()\n"},
    )
    write(tmp_path, {"lib.py": "def run(): ...\nrun = None\n"})
    snap = graph.scan(COMMIT)
    assert graph.last_parsed == ("lib.py",)
    assert summary(snap, "app:go") == {"lib:run": "rebound_name"}


def test_a_deleted_file_leaves_the_graph(tmp_path: Path) -> None:
    graph, _ = scan(tmp_path, {"a.py": "", "b.py": "import a\n"})
    (tmp_path / "a.py").unlink()
    snap = graph.scan(COMMIT)
    assert [n.id for n in snap.nodes] == ["b"]
    assert snap.edges == []


def test_a_file_that_fails_to_parse_keeps_its_last_good_parse(tmp_path: Path) -> None:
    graph, good = scan(tmp_path, {"a.py": "def f(): ...\n", "b.py": "def g(): ...\n"})
    write(tmp_path, {"a.py": "def f(:\n"})
    broken = graph.scan(COMMIT)
    assert broken.nodes == good.nodes
    assert [(e.path, e.line) for e in broken.parse_errors] == [("a.py", 1)]
    assert broken.version != good.version
    write(tmp_path, {"a.py": "def f2(): ...\n"})
    fixed = graph.scan(COMMIT)
    assert fixed.parse_errors == []
    assert "a:f2" in {n.id for n in fixed.nodes}


def test_a_file_that_never_parsed_is_listed_without_nodes(tmp_path: Path) -> None:
    _, snap = scan(tmp_path, {"bad.py": "class :\n", "ok.py": ""})
    assert [n.id for n in snap.nodes] == ["ok"]
    assert [e.path for e in snap.parse_errors] == ["bad.py"]


def test_a_module_defined_twice_keeps_the_first_path(tmp_path: Path) -> None:
    _, snap = scan(tmp_path, {"pkg/__init__.py": "def a(): ...\n", "pkg.py": "def b(): ...\n"})
    assert [n.id for n in snap.nodes] == ["pkg", "pkg:b"]
    assert [(e.path, e.message) for e in snap.parse_errors] == [
        ("pkg/__init__.py", "module pkg is already defined by pkg.py")
    ]


def test_output_is_sorted_by_id_and_independent_of_scan_history(tmp_path: Path) -> None:
    files = {
        "z.py": "from a import f\ndef y():\n    f()\n",
        "a.py": "def f(): ...\ndef b():\n    f()\n",
    }
    graph, snap = scan(tmp_path, files)
    assert [n.id for n in snap.nodes] == sorted(n.id for n in snap.nodes)
    assert [e.id for e in snap.edges] == sorted(e.id for e in snap.edges)
    assert snap.positions == {}
    assert snap.commit == COMMIT
    write(tmp_path, {"a.py": "def f(): ...\n"})
    graph.scan(COMMIT)
    write(tmp_path, files)
    assert graph.scan(COMMIT) == WorkspaceGraph(tmp_path).scan(COMMIT) == snap
