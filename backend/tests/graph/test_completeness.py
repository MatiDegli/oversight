"""Completeness evidence is independent of cached last-good parses and scan exclusions."""

import hashlib
from pathlib import Path

from oversight.graph import WorkspaceGraph, graph_completeness
from oversight.limits import MAX_FILE_BYTES


def test_every_status_and_scan_exclusions(tmp_path: Path) -> None:
    files = {
        "ok.py": b"def f(): return 1\n",
        "large.py": b"#" * (MAX_FILE_BYTES + 1),
        "syntax.py": b"def f(:\n",
        "encoding.py": b"# coding: utf-8\nx = '\xff'\n",
        "bad-name.py": b"def f(): pass\n",
        "bad-dir/module.py": b"",
        "__init__.py": b"",
        "pkg.py": b"def f(:\n",
        "pkg/__init__.py": b"",
        ".hidden/module.py": b"invalid",
        ".hidden.py": b"invalid",
        "__pycache__/module.py": b"invalid",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (tmp_path / "link.py").symlink_to(tmp_path / "syntax.py")
    (tmp_path / "linked").symlink_to(tmp_path / "pkg", target_is_directory=True)
    graph = WorkspaceGraph(tmp_path)
    snapshot = graph.scan("a" * 40)
    last = graph.last_parsed
    report = graph_completeness(tmp_path)
    assert {f.path: f.status for f in report} == {
        "ok.py": "parsed",
        "large.py": "size",
        "syntax.py": "syntax",
        "encoding.py": "encoding",
        "bad-name.py": "unimportable",
        "bad-dir/module.py": "unimportable",
        "__init__.py": "unimportable",
        "pkg/__init__.py": "parsed",
    }
    assert [f.path for f in report] == sorted(f.path for f in report)
    assert all(f.sha256 == hashlib.sha256(files[f.path]).hexdigest() for f in report)
    assert graph.last_parsed == last
    assert graph.scan("a" * 40) == snapshot


def test_stale_parse_is_a_gap_and_report_never_mutates_graph(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    path.write_text("def f(): return 1\n")
    graph = WorkspaceGraph(tmp_path)
    first = graph.scan("a" * 40)
    path.write_text("def f(:\n")
    broken = graph.scan("a" * 40)
    assert broken.nodes == first.nodes
    assert graph_completeness(tmp_path)[0].status == "syntax"
    assert graph.node_at("module.py", 1) == "module:f"
    path.write_text("def g(): return 2\n")
    assert graph_completeness(tmp_path)[0].status == "parsed"
    assert graph.node_at("module.py", 1) == "module:f"
    graph.scan("a" * 40)
    assert graph.node_at("module.py", 1) == "module:g"


def test_declared_encoding_is_parsed_and_unknown_encoding_is_a_gap(tmp_path: Path) -> None:
    (tmp_path / "latin.py").write_bytes(b"# coding: latin-1\nx = '\xe9'\n")
    (tmp_path / "unknown.py").write_bytes(b"# coding: no-such-encoding\nx = 1\n")
    WorkspaceGraph(tmp_path).scan("a" * 40)
    assert {f.path: f.status for f in graph_completeness(tmp_path)} == {
        "latin.py": "parsed",
        "unknown.py": "encoding",
    }


def test_nt_completeness_matches_posix_and_excludes_reparse(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    from oversight.graph import _workspace

    (tmp_path / "a.py").write_bytes(b"def f(): pass\n")
    (tmp_path / "bad-name.py").write_bytes(b"def f(): pass\n")
    expected = graph_completeness(tmp_path)
    (tmp_path / "junction").mkdir()
    (tmp_path / "junction/canary.py").write_bytes(b"CANARY")
    real = os.lstat

    def reparse(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if Path(path).name == "junction":
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(os, "lstat", reparse)
    assert graph_completeness(tmp_path, nt=True) == expected
    monkeypatch.setattr(_workspace, "sys", SimpleNamespace(platform="win32"))
    assert graph_completeness(tmp_path, nt=True) == expected
