"""Static editable translation on Linux, separately recomputed for every tested tree."""

import json
import ntpath
import os
from dataclasses import replace

import pytest

from oversight.workspace import source_nt as nt

from .test_source_nt_env import environment


def mapped_environment(tmp_path, layout="backend/src", *, finder=False):
    source, tree, io = environment(tmp_path)
    source = replace(source, source_path="C:/source project")
    base_bind = io.bind
    io.bind = lambda raw: (
        source.root / ntpath.relpath(raw, source.source_path).replace("\\", "/")
        if ntpath.normcase(raw).startswith(ntpath.normcase(source.source_path))
        else base_bind(raw)
    )
    site = source.root / ".venv/Lib/site-packages"
    info = site / "different_distribution-1.dist-info"
    info.mkdir()
    (info / "direct_url.json").write_text(
        json.dumps({"url": "file:///C:/source%20project", "dir_info": {"editable": True}})
    )
    (info / "top_level.txt").write_text("sample\n")
    for root in (source.root, tree):
        package = root / layout / "sample"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("value = 1\n")
    if finder:
        (site / "__editable__own_finder.py").write_text(
            f"MAPPING: dict = {{'sample': {source.source_path + '/' + layout + '/sample'!r}}}\n"
            "NAMESPACES = {}\nraise RuntimeError('must never execute in server')\n"
        )
        (site / "own.pth").write_text(
            "import __editable__own_finder; __editable__own_finder.install()\n"
        )
    else:
        (site / "own.pth").write_text(os.path.relpath(source.root / layout, site) + "\n")
    return source, tree, io, site


@pytest.mark.parametrize("layout", ["src", "lib", "backend/src", "packages/foo/src"])
@pytest.mark.parametrize("finder", [False, True])
def test_layout_and_metadata_names_are_translated_per_tree(tmp_path, layout, finder):
    source, main, io, _ = mapped_environment(tmp_path, layout, finder=finder)
    for tree in (main, source.runtime / "worktrees/agent-1", source.runtime / "tmp/combined"):
        if tree != main:
            package = tree / layout / "sample"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("value = 2\n")
        own = nt.prepare_environment(source, tree, io.deadline, io=io)
        assert own.roots == (tree / layout,)
        assert own.guard is not None
        assert json.loads(own.guard)["packages"] == ["sample"]
        assert str(source.root / layout) not in own.guard
        assert json.loads(own.guard)["tree"] == str(tree)


def test_namespace_literals_and_unrelated_finder_are_supported(tmp_path):
    source, tree, io, site = mapped_environment(tmp_path, finder=True)
    for root in (source.root, tree):
        (root / "lib/ns/child").mkdir(parents=True)
    (site / "__editable__namespace.py").write_text(
        f"NAMESPACES = {{'ns': [{source.source_path + '/lib/ns'!r}], "
        f"'ns.child': [{source.source_path + '/lib/ns/child'!r}]}}"
    )
    (site / "__editable__unrelated.py").write_text(
        "MAPPING = {'unrelated': 'D:/elsewhere/pkg'}\nNAMESPACES = {}"
    )
    roots, names = nt.editable_imports(source, tree, io)
    assert roots == (tree / "backend/src", tree / "lib")
    assert names == ("ns", "ns.child", "sample")


@pytest.mark.parametrize("layout", ["src", "lib", "backend/src", "packages/foo/src"])
@pytest.mark.parametrize("details", [{"dir_info": {"editable": False}}, {}, {"archive_info": {}}])
def test_noneditable_project_snapshot_without_mapping_is_unavailable(tmp_path, layout, details):
    source, tree, io, site = mapped_environment(tmp_path, layout)
    (site / "own.pth").unlink()
    (site / "different_distribution-1.dist-info/direct_url.json").write_text(
        json.dumps({"url": "file:///C:/source%20project", **details})
    )
    snapshot = site / "sample"
    snapshot.mkdir()
    (snapshot / "__init__.py").write_text("value = 'installed snapshot'\n")
    before = {p: p.read_bytes() for p in source.root.rglob("*") if p.is_file()}
    with pytest.raises(OSError, match="unsupported or unsafe editable metadata"):
        nt.prepare_environment(source, tree, io.deadline, io=io)
    assert before == {p: p.read_bytes() for p in source.root.rglob("*") if p.is_file()}


def test_noneditable_project_requires_mapping_but_dependencies_do_not(tmp_path):
    source, tree, io, site = mapped_environment(tmp_path)
    (site / "different_distribution-1.dist-info/direct_url.json").write_text(
        json.dumps({"url": "file:///C:/source%20project/backend"})
    )
    for name, url in (
        ("remote", "https://example.invalid/dependency.whl"),
        ("external", "file:///D:/dependency"),
        ("venv", "file:///C:/source%20project/.venv/Lib/site-packages"),
    ):
        info = site / (name + "-1.dist-info")
        info.mkdir()
        (info / "direct_url.json").write_text(json.dumps({"url": url}))
        (info / "top_level.txt").write_text("unrelated_dependency\n")
    own = nt.prepare_environment(source, tree, io.deadline, io=io)
    assert own.roots == (tree / "backend/src",)
    assert own.guard is not None
    assert json.loads(own.guard)["packages"] == ["sample"]


@pytest.mark.parametrize("names", [None, "", "other_package\n"])
def test_other_mapping_cannot_hide_unmapped_project_snapshot(tmp_path, names):
    source, tree, io, site = mapped_environment(tmp_path)
    info = site / "other_distribution-1.dist-info"
    info.mkdir()
    (info / "direct_url.json").write_text(
        json.dumps({"url": "file:///C:/source%20project/backend"})
    )
    if names is not None:
        (info / "top_level.txt").write_text(names)
    with pytest.raises(OSError, match="unsupported or unsafe editable metadata"):
        nt.prepare_environment(source, tree, io.deadline, io=io)


@pytest.mark.parametrize(
    "fault",
    ["url-only", "unmapped", "dynamic", "malformed", "oversized", "link", "hook", "target-link"],
)
def test_unsupported_or_unsafe_metadata_refuses_without_execution(tmp_path, fault):
    source, tree, io, site = mapped_environment(tmp_path, finder=True)
    pth, finder = site / "own.pth", site / "__editable__own_finder.py"
    if fault == "url-only":
        pth.unlink()
        finder.unlink()
    elif fault == "unmapped":
        (site / "different_distribution-1.dist-info/top_level.txt").write_text("absent\n")
    elif fault == "target-link":
        package = tree / "backend/src/sample"
        package.rename(package.with_name("moved"))
        package.symlink_to(package.with_name("moved"), target_is_directory=True)
    elif fault == "link":
        finder.rename(site / "real.py")
        finder.symlink_to(site / "real.py")
    else:
        finder.write_text(
            {
                "dynamic": "MAPPING = dict(sample='C:/source project/backend/src/sample')",
                "malformed": "MAPPING = {",
                "oversized": "x" * (nt.MAX_FILE + 1),
                "hook": "MAPPING = {}",
            }[fault]
        )
        if fault == "hook":
            pth.write_text("import __editable__own_finder; execute()")
    with pytest.raises(OSError, match="environment unavailable"):
        nt.prepare_environment(source, tree, io.deadline, io=io)


@pytest.mark.parametrize("layout", ["src", "lib", "backend/src", "packages/foo/src"])
@pytest.mark.parametrize("entry", ["package", "module", "namespace"])
def test_path_line_without_top_level_names(tmp_path, layout, entry):
    source, main, io, site = mapped_environment(tmp_path, layout)
    info = site / "different_distribution-1.dist-info"
    (info / "top_level.txt").unlink()
    (info / "RECORD").write_text("own.pth,,\n")
    for tree in (main, source.runtime / "worktrees/a", source.runtime / "tmp/combined"):
        root = tree / layout
        root.mkdir(parents=True, exist_ok=True)
        if tree != main:
            (root / "sample").mkdir()
        if entry == "module":
            (root / "extra.py").write_text("raise RuntimeError('never import')")
        else:
            (root / "extra").mkdir()
            if entry == "package":
                (root / "extra/__init__.py").write_text("raise RuntimeError('never import')")
        own = nt.prepare_environment(source, tree, io.deadline, io=io)
        assert own.guard is not None
        assert json.loads(own.guard)["packages"] == ["extra", "sample"]
        assert own.roots == (root,)


@pytest.mark.parametrize("fault", ["unsafe", "link", "entries", "deadline", "association"])
def test_path_discovery_is_bounded_and_associated(tmp_path, monkeypatch, fault):
    source, tree, io, site = mapped_environment(tmp_path)
    info = site / "different_distribution-1.dist-info"
    (info / "top_level.txt").unlink()
    (info / "RECORD").write_text("other.pth,,\n" if fault == "association" else "own.pth,,\n")
    root = tree / "backend/src"
    if fault == "unsafe":
        (root / "bad:name.py").touch()
    elif fault == "link":
        (root / "linked.py").symlink_to(root / "sample/__init__.py")
    elif fault == "entries":
        monkeypatch.setattr("oversight.workspace.metadata_io.MAX_ENTRIES", 4)
    elif fault == "deadline":
        io.deadline = 0
    with pytest.raises(OSError, match="environment unavailable"):
        nt.prepare_environment(source, tree, io.deadline, io=io)
