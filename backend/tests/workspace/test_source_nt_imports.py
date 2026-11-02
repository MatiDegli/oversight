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
