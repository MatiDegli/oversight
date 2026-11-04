"""Original POSIX environments are read, never activated, repaired or synchronized."""

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from oversight.workspace import source_posix_env as env
from oversight.workspace.locks import offload
from oversight.workspace.runner import ProcessResult, run_process

from .test_source_posix import repository, runner_cleanup_witness, witness


async def environment(tmp_path: Path):
    source = await repository(tmp_path)
    result = await run_process(
        [sys.executable, "-I", "-S", "-m", "venv", "--without-pip", str(source.root / ".venv")],
        cwd=tmp_path,
        timeout_s=20,
    )
    assert result.returncode == 0, result.stderr
    tree = source.runtime / "tree"
    tree.mkdir()
    return source, tree


def test_posix_environment_validation(tmp_path):
    async def scenario():
        source, tree = await environment(tmp_path)
        before = witness(source.root)
        prepared = await env.prepare_environment(source, tree)
        assert prepared.python == source.root / ".venv/bin/python"
        assert prepared.base_python.is_absolute()
        assert prepared.roots == ()
        assert prepared.policy.source == source.root
        await env.verify_environment(prepared)
        assert witness(source.root) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "attack",
    [
        "python",
        "config",
        "system-site",
        "base",
        "cycle",
        "unsafe",
        "mutation",
        "relative",
        "duplicate",
    ],
)
def test_environment_refusals(tmp_path, attack):
    async def scenario():
        source, tree = await environment(tmp_path)
        venv = source.root / ".venv"
        config = venv / "pyvenv.cfg"
        if attack == "python":
            (venv / "bin/python").unlink()
        elif attack == "config":
            config.unlink()
        elif attack == "system-site":
            config.write_text(config.read_text().replace("false", "true"))
        elif attack == "base":
            config.write_text(config.read_text().replace("executable = ", "executable = /invalid"))
        elif attack in {"cycle", "unsafe", "relative"}:
            python = venv / "bin/python"
            python.unlink()
            if attack == "cycle":
                python.symlink_to("python")
            elif attack == "relative":
                python.symlink_to("../../../a.py")
            else:
                target = source.root / "unsafe-python"
                target.write_text("unsafe")
                target.chmod(0o777)
                python.symlink_to(target)
        elif attack == "duplicate":
            config.write_text(config.read_text() + "\nhome = /another\n")
        if attack == "mutation":
            prepared = await env.prepare_environment(source, tree)
            config.write_text(config.read_text() + "\n# mutation\n")
            with pytest.raises(OSError):
                await env.verify_environment(prepared)
        else:
            before = witness(source.root)
            with pytest.raises(OSError):
                await env.prepare_environment(source, tree)
            assert witness(source.root) == before

    asyncio.run(scenario())


async def editable_fixture(tmp_path: Path, kind: str):
    source, tree = await environment(tmp_path)
    for root in (source.root, tree):
        (root / "src/pkg").mkdir(parents=True)
        (root / "src/pkg/__init__.py").write_text("VALUE = 'disposable'\n")
    (source.root / "src/pkg/__init__.py").write_text("raise AssertionError('original imported')\n")
    site = next((source.root / ".venv/lib").glob("python*/site-packages"))
    distribution = site / "fixture-1.dist-info"
    distribution.mkdir()
    (distribution / "direct_url.json").write_text(
        json.dumps({"url": source.root.as_uri(), "dir_info": {"editable": True}})
    )
    (distribution / "top_level.txt").write_text("pkg\n")
    (distribution / "RECORD").write_text("editable.pth,,\n")
    if kind == "path":
        (site / "editable.pth").write_text(str(source.root / "src") + "\n")
    else:
        (site / "editable.pth").write_text(
            "import __editable___fixture; __editable___fixture.install()\n"
        )
        (site / "__editable___fixture.py").write_text(
            "raise AssertionError('finder executed')\nMAPPING = "
            + repr({"pkg": str(source.root / "src/pkg")})
            + "\nNAMESPACES = {}\n"
        )
    return source, tree, site, distribution


@pytest.mark.parametrize("kind", ["path", "finder"])
def test_posix_editable_mapping(tmp_path, kind):
    async def scenario():
        source, tree, site, _ = await editable_fixture(tmp_path, kind)
        before = witness(source.root)
        prepared = await env.prepare_environment(source, tree)
        assert prepared.roots == (tree / "src",)
        assert prepared.policy.packages == ("pkg",)
        # -I -S skips every original .pth/finder; import only explicitly translated roots.
        code = (
            "import sys; sys.path[:0] = "
            + repr([str(p) for p in prepared.roots])
            + ("; import pkg; print(pkg.__file__); print(pkg.VALUE)")
        )
        result = await run_process(
            [str(prepared.python), "-I", "-S", "-B", "-c", code], cwd=tree, timeout_s=10
        )
        assert result.returncode == 0, result.stderr
        assert str(tree / "src/pkg/__init__.py") in result.stdout
        assert "disposable" in result.stdout
        assert not tuple(site.rglob("__pycache__"))
        assert witness(source.root) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "attack", ["escape", "missing", "unassociated", "malformed", "hook", "incomplete"]
)
def test_editable_refusals(tmp_path, attack):
    async def scenario():
        source, tree, site, distribution = await editable_fixture(tmp_path, "path")
        if attack == "escape":
            (site / "editable.pth").write_text("/outside\n")
        elif attack == "missing":
            (site / "editable.pth").unlink()
        elif attack == "unassociated":
            (distribution / "RECORD").write_text("unrelated.pth,,\n")
        elif attack == "malformed":
            (distribution / "direct_url.json").write_text("[]")
        elif attack == "hook":
            (site / "editable.pth").write_text("import malicious\n")
        elif attack == "incomplete":
            (distribution / "top_level.txt").write_text("pkg\nmissing\n")
        before = witness(source.root)
        with pytest.raises(OSError):
            await env.prepare_environment(source, tree)
        assert witness(source.root) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["start", "failed", "timeout", "truncated", "malformed", "cancel"]
)
def test_environment_process_cleanup(tmp_path, failure):
    async def scenario():
        source, tree = await environment(tmp_path)
        before = witness(source.root)

        async def spawn(argv, **kwargs):
            assert "-I" in argv and "-S" in argv and "-B" in argv
            assert kwargs["timeout_s"] <= env.PHASE_SECONDS
            if failure == "start":
                raise OSError("start")
            if failure == "cancel":
                raise asyncio.CancelledError
            return ProcessResult(
                1 if failure == "failed" else 0,
                "bad",
                "",
                failure == "timeout",
                failure == "truncated",
                0,
            )

        with pytest.raises(asyncio.CancelledError if failure == "cancel" else OSError):
            await env.prepare_environment(source, tree, spawn=spawn)
        assert witness(source.root) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "attack",
    [
        "finder-syntax",
        "finder-escape",
        "finder-missing",
        "finder-shape",
        "target-link",
        "target-missing",
        "url",
        "legacy",
        "metadata-link",
        "budget",
        "tree-alias",
    ],
)
def test_editable_metadata_safety(tmp_path, monkeypatch, attack):
    async def scenario():
        source, tree, site, distribution = await editable_fixture(tmp_path, "finder")
        finder = site / "__editable___fixture.py"
        if attack == "finder-syntax":
            finder.write_text("MAPPING = syntax error")
        elif attack == "finder-escape":
            finder.write_text("MAPPING = {'pkg': '/outside/pkg'}")
        elif attack == "finder-missing":
            finder.unlink()
        elif attack == "finder-shape":
            finder.write_text("MAPPING = {'pkg': [1, 2]}")
        elif attack == "target-link":
            (tree / "src/pkg").rename(tree / "src/held")
            (tree / "src/pkg").symlink_to(source.root / "src/pkg", target_is_directory=True)
        elif attack == "target-missing":
            (tree / "src/pkg/__init__.py").unlink()
            (tree / "src/pkg").rmdir()
        elif attack == "url":
            (distribution / "direct_url.json").write_text(
                json.dumps({"url": "https://elsewhere.invalid", "dir_info": {"editable": True}})
            )
        elif attack == "legacy":
            (site / "pkg.egg-link").write_text(str(source.root))
        elif attack == "metadata-link":
            (site / "editable.pth").unlink()
            (site / "editable.pth").symlink_to(source.root / "a.py")
        elif attack == "budget":
            monkeypatch.setattr(env, "MAX_METADATA", 1)
        else:
            tree = source.root
        before = witness(source.root)
        with pytest.raises(OSError):
            await env.prepare_environment(source, tree)
        assert witness(source.root) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["config", "link", "pth", "finder", "tree"])
def test_environment_revalidation(tmp_path, target):
    async def scenario():
        source, tree, site, _ = await editable_fixture(tmp_path, "finder")
        prepared = await env.prepare_environment(source, tree)
        path = {
            "config": source.root / ".venv/pyvenv.cfg",
            "link": prepared.python,
            "pth": site / "editable.pth",
            "finder": site / "__editable___fixture.py",
            "tree": tree / "src/pkg",
        }[target]
        if target == "link":
            path.unlink()
            path.symlink_to("/missing")
        elif target == "tree":
            path.rename(tree / "src/held")
            path.mkdir()
        else:
            path.write_bytes(path.read_bytes() + b"\n# mutation\n")
        with pytest.raises(OSError):
            await env.verify_environment(prepared)

    asyncio.run(scenario())


def test_environment_under_runtime_inside_ide(tmp_path):
    async def scenario():
        source, _tree = await environment(tmp_path)
        # The ordinary runtime layout is inside the running checkout; only the
        # selected original must remain disjoint from both.
        source.runtime.rename(source.ide / "runtime")
        from oversight.workspace.source_posix import prepare_source

        prepared_source = await prepare_source(source.root, source.ide / "runtime", source.ide)
        await env.prepare_environment(prepared_source, source.ide / "runtime/tree")

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_real_environment_runner_cleanup(tmp_path, mode):
    async def scenario():
        source, tree = await environment(tmp_path)
        before = witness(source.root)

        async def operation(spawn):
            return await env.prepare_environment(source, tree, spawn=spawn)

        await runner_cleanup_witness(tmp_path, operation, mode)
        assert witness(source.root) == before

    asyncio.run(scenario())


def test_uv_hook_is_never_executed(tmp_path):
    async def scenario():
        source, tree, site, _ = await editable_fixture(tmp_path, "path")
        (site / "_virtualenv.pth").write_text("import _virtualenv\n")
        (site / "_virtualenv.py").write_text("raise AssertionError('original hook executed')\n")
        before = witness(source.root)
        prepared = await env.prepare_environment(source, tree)
        assert prepared.roots == (tree / "src",)
        assert prepared.policy.no_site
        assert witness(source.root) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["metadata", "config", "copy"])
def test_environment_link_policy(tmp_path, kind):
    async def scenario():
        source, tree, site, distribution = await editable_fixture(tmp_path, "path")
        if kind == "copy":
            python = source.root / ".venv/bin/python"
            resolved = python.resolve()
            python.unlink()
            shutil.copyfile(resolved, python)
            python.chmod(0o700)
        elif kind == "metadata":
            os.link(distribution / "direct_url.json", tmp_path / "cached-metadata")
            os.link(site / "editable.pth", tmp_path / "cached-pth")
        else:
            os.link(source.root / ".venv/pyvenv.cfg", tmp_path / "linked-config")
        before = witness(source.root)
        if kind == "config":
            with pytest.raises(OSError):
                await env.prepare_environment(source, tree)
        else:
            prepared = await env.prepare_environment(source, tree)
            await env.verify_environment(prepared)
            assert prepared.roots == (tree / "src",)
        assert witness(source.root) == before

    asyncio.run(scenario())


def test_interpreter_link_parent_semantics(tmp_path):
    async def scenario():
        source, tree = await environment(tmp_path)
        python = source.root / ".venv/bin/python"
        base = await offload(lambda: Path(sys.executable).resolve())
        base_home = base.parent
        (python.parent / "route").symlink_to(base_home, target_is_directory=True)
        python.unlink()
        python.symlink_to("route/../bin/python3.12")
        before = witness(source.root)
        io = env._IO(time.monotonic() + 10, source.root / ".venv/lib/python3.12/site-packages")
        resolved, _ = await offload(lambda: io.resolve_interpreter(python))
        assert resolved == base
        # CPython 3.12 itself reports a nonexistent _base_executable for this unusual
        # link. Correct POSIX resolution cannot waive an inconsistent runtime probe.
        with pytest.raises(OSError):
            await env.prepare_environment(source, tree)
        assert witness(source.root) == before

    asyncio.run(scenario())


def test_unsafe_interpreter_parent_target_refuses_before_execution(tmp_path):
    async def scenario():
        source, tree = await environment(tmp_path)
        python = source.root / ".venv/bin/python"
        unsafe = tmp_path / "another-base"
        (unsafe / "inner").mkdir(parents=True)
        base = await offload(lambda: Path(sys.executable).resolve())
        shutil.copyfile(base, unsafe / "python3.12")
        (unsafe / "python3.12").chmod(0o700)
        (python.parent / "route").symlink_to(unsafe / "inner", target_is_directory=True)
        python.unlink()
        python.symlink_to("route/../python3.12")

        async def spawn(argv, **kwargs):
            raise AssertionError("inconsistent target reached execution")

        before = witness(source.root)
        with pytest.raises(OSError):
            await env.prepare_environment(source, tree, spawn=spawn)
        assert witness(source.root) == before

    asyncio.run(scenario())


def test_editable_parent_link_is_not_lexically_erased(tmp_path):
    async def scenario():
        source, tree, site, _ = await editable_fixture(tmp_path, "path")
        (source.root / "src/route").symlink_to(tmp_path, target_is_directory=True)
        (site / "editable.pth").write_text(str(source.root / "src/route/..") + "\n")
        with pytest.raises(OSError):
            await env.prepare_environment(source, tree)

    asyncio.run(scenario())
