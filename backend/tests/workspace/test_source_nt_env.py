"""Original-venv policy and deadline injections; no native acceptance."""

import asyncio
import json
import ntpath
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from oversight.workspace import source_nt as nt
from oversight.workspace.runner import ProcessResult, run_process
from oversight.workspace.suite import (
    SuiteInterrupted,
    SuiteUnavailable,
    _probe_environment,
    run_suite,
)

from .test_source_nt_reset import opened


@pytest.mark.anyio
@pytest.mark.parametrize(("platform", "separator"), [("win32", ";"), ("linux", ":")])
async def test_readiness_suite_environment_agreement(tmp_path, monkeypatch, platform, separator):
    from oversight.workspace import core, runner, suite

    for name in ("META_API_KEY", "baseten_api_key", "ARBITRARY_CREDENTIAL"):
        monkeypatch.setenv(name, "must not inherit")
    async with opened(tmp_path) as (workspace, source, _):
        monkeypatch.setattr(suite, "sys", SimpleNamespace(platform=platform))
        monkeypatch.setattr(runner, "sys", SimpleNamespace(platform=platform))
        # Separate readiness/suite deadlines; retain a real epoch for Git integrity leases.
        now = float(int(time.monotonic()))
        monkeypatch.setattr(core, "time", SimpleNamespace(monotonic=lambda: now))
        workspace._own_source = source
        tree = workspace.main.root
        own = nt.OwnEnvironment(
            source.root / ".venv/Scripts/python.exe", (tree / "backend/src",), guard_policy(tree)
        )
        deadlines, environments = [], []

        def prepare(root, deadline):
            assert root == tree
            deadlines.append(deadline)
            return own

        async def spawn(argv, *, cwd, timeout_s, env=None, **kwargs):
            assert env is not None
            assert cwd == tree and argv[0] == str(own.python)
            assert not any("credential" in k.lower() or "api_key" in k.lower() for k in env)
            assert not any(
                "credential" in k.lower() or "api_key" in k.lower() for k in runner.child_env(env)
            )
            environments.append(dict(env))
            if argv[1] == "-c":
                assert timeout_s == 10
                return ProcessResult(0, '{"ok":true}', "", False, False, 1)
            assert timeout_s == 27
            await asyncio.to_thread(
                Path(env["OVERSIGHT_REPORT"]).write_text, '{"kind":"guard","ok":true}\n'
            )
            return ProcessResult(0, "", "", False, False, 1)

        monkeypatch.setattr(workspace, "prepare_own_environment", prepare)
        monkeypatch.setattr(core, "run_process", spawn)
        assert await workspace.own_environment_readiness() is None
        result = await run_suite(
            python=Path("unused"),
            plugin_dir=core.PLUGIN_DIR,
            runtime_root=source.runtime,
            tree_root=tree,
            timeout_s=27,
            redact=lambda s: s,
            spawn=spawn,
            _own_prepare=prepare,
            _clock=lambda: now,
        )
        assert result.result.outcome == "passed"
        assert deadlines == [now + 10, now + 27]
        readiness, probe, execution = environments
        assert probe == execution
        assert probe.pop("OVERSIGHT_REPORT")
        assert readiness == probe
        assert readiness["PYTHONPATH"] == separator.join(
            map(str, (core.PLUGIN_DIR, tree, *own.roots))
        )
        assert readiness["HOME"] == readiness["TEMP"] == readiness["TMP"]
        assert readiness["PYTHONHASHSEED"] == "0"
        assert readiness["OVERSIGHT_IMPORT_GUARD"] == own.guard
        assert readiness["OVERSIGHT_TRUSTED_PLUGIN"] == str(core.PLUGIN_DIR / "oversight_report.py")
        assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in readiness


def guard_policy(tree, source=None):
    return json.dumps(
        dict(
            tree=str(tree),
            source=str(source or tree / "original"),
            libraries=[str(Path(sys.prefix) / ("Lib" if sys.platform == "win32" else "lib"))],
            packages=[],
        )
    )


def environment(tmp_path: Path):
    source = nt.NtSource(tmp_path / "source", tmp_path / "runtime", "C:/source", "C:/runtime")
    base, tree = tmp_path / "base", source.runtime / "workspace"
    for path in (
        source.root / ".venv/Scripts",
        source.root / ".venv/Lib/site-packages",
        base / "Lib",
        tree,
    ):
        path.mkdir(parents=True)
    for path in (source.root / ".venv/Scripts/python.exe", base / "python.exe"):
        path.write_bytes(b"launcher")
    (source.root / ".venv/pyvenv.cfg").write_text(
        "home = C:/base\ninclude-system-site-packages = false\nexecutable = C:/base/python.exe\n"
    )
    io = nt.EnvironmentIO(
        time.monotonic() + 20,
        preflight=lambda _: None,
        bind=lambda raw: base / ntpath.relpath(raw, "C:/base").replace("\\", "/"),
        sensitive=lambda _: False,
        streams=lambda _: iter_streams(),
    )
    return source, tree, io


def iter_streams():
    yield "::$DATA"


@pytest.mark.parametrize(
    "name",
    [
        ".venv",
        ".venv/Scripts",
        ".venv/Lib",
        ".venv/Lib/site-packages",
        ".venv/Scripts/python.exe",
        ".venv/pyvenv.cfg",
        "base",
        "base/Lib",
        "base/python.exe",
    ],
)
@pytest.mark.parametrize("fault", ["missing", "link", "placement"])
def test_unsafe_original_venv_has_no_fallback(tmp_path, name, fault):
    source, tree, io = environment(tmp_path)
    path = tmp_path / name if name.startswith("base") else source.root / name
    if fault == "placement":

        def refuse(raw):
            if raw == str(path if path.is_dir() else path.parent):
                raise OSError("private path and credentials must not escape")

        io.preflight = refuse
    else:
        moved = path.with_name(path.name + "-original")
        path.rename(moved)
        if fault == "link":
            path.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(
        OSError, match=r"^Own-source environment unavailable: missing or unsafe original venv$"
    ):
        nt.prepare_environment(source, tree, io.deadline, io=io)
    assert not list(source.runtime.glob("demo-venv*"))


@pytest.mark.parametrize(
    "fault", ["streams", "sensitive", "hardlink", "reparse", "alias", "cfg", "oversized", "base"]
)
def test_environment_nt_boundaries(tmp_path, monkeypatch, fault):
    source, tree, io = environment(tmp_path)
    config = source.root / ".venv/pyvenv.cfg"
    if fault == "streams":
        io.streams = lambda _: (name for name in ("::$DATA", ":secret:$DATA"))
    elif fault == "sensitive":
        io.sensitive = lambda _: True
    elif fault == "hardlink":
        os.link(config, config.with_name("linked"))
    elif fault == "reparse":
        inode = config.stat().st_ino
        monkeypatch.setattr(
            "oversight.workspace.files.is_link_like", lambda info, **_: info.st_ino == inode
        )
    elif fault == "alias":
        io.bind = lambda _: tmp_path / "base~1"
    else:
        config.write_text(
            {
                "cfg": "include-system-site-packages = true",
                "oversized": "x" * (nt.MAX_FILE + 1),
                "base": "home = C:/base:stream\ninclude-system-site-packages = false",
            }[fault]
        )
    before = config.read_bytes()
    with pytest.raises(OSError, match="missing or unsafe original venv"):
        nt.prepare_environment(source, tree, io.deadline, io=io)
    assert config.read_bytes() == before


def test_environment_uses_original_launcher_without_writes(tmp_path):
    source, tree, io = environment(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    prepared = nt.prepare_environment(source, tree, io.deadline, io=io)
    assert prepared.python == source.root / ".venv/Scripts/python.exe"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.anyio
@pytest.mark.parametrize(("budget", "elapsed"), [(4, 1.25), (27, 3), (2, 3)])
async def test_own_environment_and_one_deadline(tmp_path, monkeypatch, budget, elapsed):
    (tmp_path / "tmp").mkdir()
    monkeypatch.setenv("ARBITRARY_CREDENTIAL", "must not inherit")
    now, calls = [100.0], []
    python, plugin = tmp_path / "source/.venv/Scripts/python.exe", tmp_path / "trusted"

    def prepare(tree, deadline):
        assert tree == tmp_path and deadline == 100 + budget
        now[0] += 0.5  # preparation consumes the same allowance
        return nt.OwnEnvironment(python, (tmp_path / "backend/src",), guard_policy(tmp_path))

    async def spawn(argv, *, cwd, timeout_s, env=None, output_cap=262144):
        assert env is not None and cwd == tmp_path and argv[0] == str(python)
        assert env == {
            "HOME": str(tmp_path / "tmp/home"),
            "TEMP": str(tmp_path / "tmp/home"),
            "TMP": str(tmp_path / "tmp/home"),
            "PYTHONPATH": os.pathsep.join(map(str, (plugin, tmp_path, tmp_path / "backend/src"))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "OVERSIGHT_REPORT": env["OVERSIGHT_REPORT"],
            "OVERSIGHT_TRUSTED_PLUGIN": str(plugin / "oversight_report.py"),
            "OVERSIGHT_IMPORT_GUARD": guard_policy(tmp_path),
        }
        calls.append((argv, timeout_s))
        if argv[1] == "-c":
            assert timeout_s == min(budget - 0.5, 10)
            now[0] += elapsed
            return ProcessResult(0, '{"ok":true}', "", False, False, 1)
        assert timeout_s == budget - 0.5 - elapsed
        await asyncio.to_thread(
            Path(env["OVERSIGHT_REPORT"]).write_text, '{"kind":"guard","ok":true}\n'
        )
        return ProcessResult(0, "", "", False, False, 1)

    async def suite():
        return await run_suite(
            python=Path("/no-fallback"),
            plugin_dir=plugin,
            runtime_root=tmp_path,
            tree_root=tmp_path,
            timeout_s=budget,
            redact=lambda s: s,
            spawn=spawn,
            _own_prepare=prepare,
            _clock=lambda: now[0],
        )

    if elapsed + 0.5 >= budget:
        with pytest.raises(SuiteUnavailable) as error:
            await suite()
        assert "deadline expired" in str(error.value.cause)
        assert len(calls) == 1
    else:
        assert (await suite()).result.outcome == "passed"
        assert len(calls) == 2
    assert not list((tmp_path / "tmp").iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fault", ["versions", "missing", "exit", "truncated", "timeout", "garbage", "launch", "cancel"]
)
async def test_probe_failure_is_bounded_and_cleanup_runs(tmp_path, fault):
    (tmp_path / "tmp").mkdir()

    async def spawn(argv, *, env=None, **kwargs):
        assert env is not None
        await asyncio.to_thread(
            Path(env["OVERSIGHT_REPORT"]).write_text, '{"kind":"guard","ok":true}\n'
        )
        if fault == "launch":
            raise OSError("secret C:/private")
        if fault == "cancel":
            raise asyncio.CancelledError
        output = json.dumps(
            {
                "ok": fault in {"truncated", "timeout", "exit"},
                "python": "3.9.9",
                "pytest": "7.0",
                "pluggy": "missing",
            }
        )
        return ProcessResult(
            1 if fault == "exit" else 0,
            "secret C:/private" if fault == "garbage" else output,
            "private",
            fault == "timeout",
            fault == "truncated",
            1,
        )

    with pytest.raises(SuiteInterrupted if fault == "cancel" else SuiteUnavailable) as error:
        await run_suite(
            python=Path("unused"),
            plugin_dir=tmp_path,
            runtime_root=tmp_path,
            tree_root=tmp_path,
            timeout_s=7,
            redact=lambda s: s,
            spawn=spawn,
            _own_prepare=lambda *_: nt.OwnEnvironment(
                Path(sys.executable), guard=guard_policy(tmp_path)
            ),
        )
    if isinstance(error.value, SuiteUnavailable):
        if fault == "launch":
            assert error.value.own_source_reason is None
            assert str(error.value.cause) == "secret C:/private"
        else:
            assert "private" not in str(error.value.cause) and len(str(error.value.cause)) < 200
        if fault not in {"garbage", "launch"}:
            assert "python=3.9.9, pytest=7.0, pluggy=missing" in str(error.value.cause)
    assert not list((tmp_path / "tmp").iterdir())


@pytest.mark.anyio
async def test_real_probe_pins_trusted_plugin(tmp_path):
    plugin = Path(__file__).parents[2] / "src/oversight/workspace/report_plugin"
    env = {
        "PYTHONPATH": str(plugin),
        "OVERSIGHT_TRUSTED_PLUGIN": str(plugin / "oversight_report.py"),
        "OVERSIGHT_IMPORT_GUARD": guard_policy(tmp_path),
    }
    await _probe_environment(
        Path(sys.executable), tmp_path, env, time.monotonic() + 5, time.monotonic, run_process
    )
    (tmp_path / "oversight_report.py").write_text("raise RuntimeError('shadow')")
    with pytest.raises(OSError, match="probe failed"):
        await _probe_environment(
            Path(sys.executable), tmp_path, env, time.monotonic() + 5, time.monotonic, run_process
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("python", "3.9.9"),
        ("pytest", "8.1.0"),
        ("pluggy", "1.4.0"),
        ("pytest", "missing"),
        ("pluggy", "missing"),
    ],
)
async def test_probe_checks_actual_injected_versions(tmp_path, name, version):
    plugin = Path(__file__).parents[2] / "src/oversight/workspace/report_plugin"

    async def injected(argv, **kwargs):
        prefix = (
            "import platform, importlib.metadata as m\n"
            f"platform.python_version = lambda: {version!r}\n"
            if name == "python"
            else "import importlib.metadata as m\noriginal = m.version\n"
            f"def injected(n):\n    if n != {name!r}: return original(n)\n"
            + (
                "    raise m.PackageNotFoundError(n)\n"
                if version == "missing"
                else f"    return {version!r}\n"
            )
            + "m.version = injected\n"
        )
        return await run_process([*argv[:2], prefix + argv[2]], **kwargs)

    with pytest.raises(OSError, match=name + "=" + version.replace(".", r"\.")):
        await _probe_environment(
            Path(sys.executable),
            tmp_path,
            {
                "PYTHONPATH": str(plugin),
                "OVERSIGHT_TRUSTED_PLUGIN": str(plugin / "oversight_report.py"),
                "OVERSIGHT_IMPORT_GUARD": guard_policy(tmp_path),
            },
            time.monotonic() + 5,
            time.monotonic,
            injected,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("raw", [None, "", "{}", "null", "{", '{"libraries":[]}'])
async def test_invalid_guard_never_launches(tmp_path, raw):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid guard launched a process")

    with pytest.raises(SuiteUnavailable):
        await run_suite(
            python=Path("unused"),
            plugin_dir=tmp_path,
            runtime_root=tmp_path,
            tree_root=tmp_path,
            timeout_s=3,
            redact=lambda s: s,
            spawn=forbidden,
            _own_prepare=lambda *_: nt.OwnEnvironment(Path(sys.executable), guard=raw),
        )


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["oversight_calls", "pytest", "pluggy"])
async def test_probe_refuses_shadow_before_import(tmp_path, name):
    plugin = Path(__file__).parents[2] / "src/oversight/workspace/report_plugin"
    marker = tmp_path / "executed"
    (tmp_path / (name + ".py")).write_text(f"open({str(marker)!r}, 'w').close()")
    with pytest.raises(OSError, match="probe failed"):
        await _probe_environment(
            Path(sys.executable),
            tmp_path,
            {
                "PYTHONPATH": str(plugin),
                "OVERSIGHT_TRUSTED_PLUGIN": str(plugin / "oversight_report.py"),
                "OVERSIGHT_IMPORT_GUARD": guard_policy(tmp_path),
            },
            time.monotonic() + 5,
            time.monotonic,
            run_process,
        )
    assert not marker.exists()


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["source", "tree", "libraries", "wrong-tree"])
async def test_guard_locations_refuse_before_probe(tmp_path, field):
    policy = json.loads(guard_policy(tmp_path))
    if field == "wrong-tree":
        policy["tree"] = str(tmp_path / "another-tree")
    else:
        policy[field] = ["relative"] if field == "libraries" else "relative"

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid policy location launched a process")

    with pytest.raises(SuiteUnavailable) as error:
        await run_suite(
            python=Path("unused"),
            plugin_dir=tmp_path,
            runtime_root=tmp_path,
            tree_root=tmp_path,
            timeout_s=7,
            redact=lambda s: s,
            spawn=forbidden,
            _own_prepare=lambda *_: nt.OwnEnvironment(Path("original"), guard=json.dumps(policy)),
        )
    assert error.value.own_source_reason == "invalid import guard policy"
