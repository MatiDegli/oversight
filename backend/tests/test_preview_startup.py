"""The bounded inert startup carrier shares one exact serialization with the console."""

import json
import logging
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from oversight.preview_startup import (
    PreviewStartupReport,
    serialize_preview_startup,
    validate_preview_startup,
)


def example():
    return PreviewStartupReport(
        (("python", "-m", "uvicorn"),),
        "/T",
        "reviewed",
        (),
        "fixed 30s",
        "",
        "/r/previews/id/logs/build.log",
    )


def test_preview_startup_report_bounds_and_identity():
    report = example()
    serialized = serialize_preview_startup(report)
    raw = json.loads(serialized)
    assert set(raw) == {
        "version",
        "argv",
        "cwd",
        "reason",
        "dependencies",
        "limits",
        "platform_residual",
        "build_log",
        "enable_hint",
        "agents_label",
    }
    assert raw["version"] == 1 and raw["agents_label"] == "Scripted demo agents"
    assert raw["enable_hint"] == "Enable with OVERSIGHT_PREVIEW_ENABLED=true"
    assert validate_preview_startup(serialized)
    for value in ('</script><!--"&>\u2028\u2029', '\x00\x01\n\t\\"', "/árbol/日本語"):
        encoded = serialize_preview_startup(replace(report, cwd=value))
        assert json.loads(encoded)["cwd"] == value
        assert not any(c in encoded for c in "<>&\u2028\u2029")
    baseline = len(serialized.encode())
    exact = replace(report, reason="reviewed" + "x" * (4096 - baseline))
    exact_serialized = serialize_preview_startup(exact)
    assert len(exact_serialized.encode()) == 4096
    assert validate_preview_startup(exact_serialized)
    oversized = exact_serialized.replace('"reason":"reviewed', '"reason":"reviewedx', 1)
    assert len(oversized.encode()) == 4097
    assert not validate_preview_startup(oversized)
    with pytest.raises(ValueError, match="preview startup report exceeds 4096 UTF-8 bytes"):
        serialize_preview_startup(replace(exact, reason=exact.reason + "x"))
    for value in ("<" * 800, "日" * 1500):
        with pytest.raises(ValueError, match="exceeds 4096"):
            serialize_preview_startup(replace(report, cwd=value))
    with pytest.raises(ValueError, match="preview startup report is invalid"):
        serialize_preview_startup(replace(report, cwd="\ud800"))
    assert "META_API_KEY" not in serialized and "BASETEN_API_KEY" not in serialized
    assert not validate_preview_startup(serialized.replace('"version":1', '"version":true'))
    assert not validate_preview_startup(serialized[:-1] + ',"extra":1}')
    assert not validate_preview_startup(
        serialized.replace('"version":1', '"version":1,"version":1')
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"argv": ()},
        {"argv": (("x",),) * 9},
        {"argv": ((),)},
        {"argv": (("x",) * 33,)},
        {"argv": (("",),)},
        {"argv": ((None,),)},
        {"dependencies": ("x",) * 17},
        {"dependencies": ("",)},
        {"cwd": ""},
        {"reason": ""},
        {"limits": ""},
        {"build_log": ""},
        {"platform_residual": None},
        {"enable_hint": ""},
        {"agents_label": "live agents"},
        {"version": 2},
        {"version": True},
    ],
)
def test_invalid_startup_reports_refuse_without_truncation(changes):
    with pytest.raises(ValueError, match="preview startup report is invalid"):
        serialize_preview_startup(replace(example(), **changes))


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True])
async def test_reviewed_startup_console_index_identity(tmp_path, monkeypatch, caplog, enabled):
    import logging
    import sys
    from pathlib import Path
    from tempfile import TemporaryDirectory

    import httpx
    from workspace.preview_fakes import profile
    from workspace.test_self_profile import self_checkout

    from oversight.app import create_app
    from oversight.settings import Settings

    # The positive carrier case needs bounded paths regardless of the runner's TMPDIR.
    with TemporaryDirectory(prefix="op-", dir="/tmp" if sys.platform == "linux" else None) as short:
        tmp_path = Path(short)
        runtime, _original, _, resources = await self_checkout(tmp_path, monkeypatch)
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text('<script type="module" src="/app.js"></script>')
        settings = Settings(
            runtime_dir=runtime,
            workspace_source="oversight-self",
            demo_python=Path(sys.executable),
            frontend_dist=dist,
            preview_profile=replace(profile(), enabled=enabled, interpreter=resources["python"]),
            preview_node=resources["node"],
            preview_tsc=resources["tsc"],
            preview_vite=resources["vite"],
        )
        app = create_app(settings)
        live = app.state.live
        caplog.set_level(logging.WARNING, logger="oversight.app")
        await live._reset_workspace()
        try:
            await live._prepare_reviewed_preview()
            frozen = live.preview_startup_serialized
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=f"http://127.0.0.1:{settings.port}"
            ) as client:
                index = (await client.get("/")).text
            if enabled:
                assert frozen is not None and validate_preview_startup(frozen)
                assert [r.getMessage() for r in caplog.records].count(frozen) == 1
                assert index.count(frozen) == 1
                assert live.previews.startup_report == frozen
                data = json.loads(frozen)
                assert data["argv"] == [
                    list(c.argv)
                    for c in (
                        *live.previews.profile.reviewed_app.launch.build,
                        live.previews.profile.reviewed_app.launch.start,
                    )
                ]
                assert data["build_log"].startswith(str(runtime / "previews"))
                assert data["build_log"].endswith("/logs/build.log")
                assert "edited code executes" in data["reason"]
                assert "must never expose secret marker" not in frozen
                assert not live.previews.profile.trusted_demo
                await live._prepare_reviewed_preview()
                assert live.preview_startup_serialized == frozen
            else:
                assert frozen is None and "oversight-preview-startup" not in index
                assert "Enable with OVERSIGHT_PREVIEW_ENABLED=true" in caplog.messages
        finally:
            live.workspace.release_runtime()


async def startup_app(tmp_path, monkeypatch, default_runtime):
    from workspace.preview_fakes import profile
    from workspace.test_self_profile import self_checkout

    from oversight.app import create_app
    from oversight.settings import REPO_ROOT, Settings
    from oversight.workspace import PreviewProfile
    from oversight.workspace.source import self_inventory

    # Keep the live database path short; only synthetic prepared resource paths are oversized.
    runtime, _original, source, resources = await self_checkout(tmp_path / "source", monkeypatch)
    if default_runtime:
        node = shutil.which("node")
        assert node is not None
        resources = {
            "python": Path(sys.executable),
            "node": Path(node),
            "tsc": REPO_ROOT / "frontend/node_modules/typescript/bin/tsc",
            "vite": REPO_ROOT / "frontend/node_modules/vite/bin/vite.js",
        }
    else:
        resource_root = tmp_path
        for _ in range(5):
            resource_root /= "long-resource-" * 11
        resource_root.mkdir(parents=True)
        for name, prepared in resources.items():
            target = resource_root / name
            target.write_bytes(prepared.read_bytes())
            target.chmod(0o700)
            resources[name] = target
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<script type="module" src="/app.js"></script>')
    settings = Settings(
        runtime_dir=Settings.runtime_dir if default_runtime else runtime,
        workspace_source="oversight-self",
        demo_python=Path(sys.executable),
        frontend_dist=dist,
        preview_profile=(
            PreviewProfile(
                enabled=True,
                interpreter=resources["python"],
                ports=(19681,),
                origins=("http://127.0.0.1:8710",),
            )
            if default_runtime
            else replace(profile(), interpreter=resources["python"])
        ),
        preview_node=resources["node"],
        preview_tsc=resources["tsc"],
        preview_vite=resources["vite"],
    )
    app = create_app(settings)
    if default_runtime:
        # Verify the real default paths without resetting/writing the development runtime.
        app.state.live.workspace.source_inventory = await self_inventory(source, runtime)
    return app


@pytest.mark.anyio
async def test_oversize_startup_report_refuses_only_preview(tmp_path, monkeypatch, caplog):
    import httpx

    from oversight.preview_startup import reviewed_startup_report
    from oversight.workspace import PreviewError

    app = await startup_app(tmp_path, monkeypatch, default_runtime=False)
    live = app.state.live
    settings = live._settings
    calls = []

    def forbidden_process(*args):
        calls.append(args)
        raise AssertionError("undisclosed preview must never spawn")

    live.previews.process_factory = forbidden_process
    caplog.set_level(logging.WARNING, logger="oversight.app")
    try:
        await live.startup()
        frozen = live.previews.profile
        assert frozen.reviewed_app is not None and not frozen.trusted_demo
        assert live.previews.lifecycle.profile is frozen
        build_log = settings.runtime_dir / "previews" / live.run_id / "logs/build.log"
        with pytest.raises(ValueError, match="exceeds 4096 UTF-8 bytes"):
            reviewed_startup_report(frozen, str(build_log))
        assert live.preview_startup_serialized is None
        assert live.previews.startup_report is None
        assert "Reviewed-app startup disclosure is unavailable; preview refused." in caplog.messages
        for action in (
            live.previews.start(
                run_id=live.run_id, agent_id="agent-1", revision=1, head_commit="a" * 40
            ),
            live.previews._launch(None),  # pyright: ignore[reportArgumentType]
        ):
            with pytest.raises(
                PreviewError, match="Reviewed-app startup disclosure is unavailable"
            ) as caught:
                await action
            assert caught.value.code == "not_configured"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"http://127.0.0.1:{settings.port}"
        ) as client:
            for path in ("/", "/index.html", "/settings"):
                response = await client.get(path)
                assert response.status_code == 200
                assert "oversight-preview-startup" not in response.text
            assert (await client.get("/graph")).status_code == 200
        assert calls == [] and not live.previews.has_owner
        assert "must never expose secret marker" not in caplog.text
    finally:
        await live.shutdown()


@pytest.mark.anyio
async def test_real_resources_default_runtime_report_is_disclosed(tmp_path, monkeypatch, caplog):
    import httpx

    from oversight.preview_startup import reviewed_startup_report
    from oversight.settings import REPO_ROOT, Settings
    from oversight.workspace import PreviewError

    app = await startup_app(tmp_path, monkeypatch, default_runtime=True)
    live = app.state.live
    assert live._settings.runtime_dir == Settings.runtime_dir == REPO_ROOT / ".runtime"
    caplog.set_level(logging.WARNING, logger="oversight.app")
    await live._prepare_reviewed_preview()
    frozen = live.previews.profile
    assert frozen.reviewed_app is not None and not frozen.trusted_demo
    report = live.preview_startup_serialized
    assert report is not None and len(report.encode("utf-8")) <= 4096
    assert validate_preview_startup(report)
    build_log = Settings.runtime_dir / "previews" / live.run_id / "logs/build.log"
    assert reviewed_startup_report(frozen, str(build_log)) == report
    assert live.previews.startup_report == report
    assert [record.getMessage() for record in caplog.records].count(report) == 1
    assert "Reviewed-app startup disclosure is unavailable; preview refused." not in caplog.messages
    data = json.loads(report)
    assert data["cwd"].startswith(str(Settings.runtime_dir / "previews" / live.run_id / "tree"))
    assert data["argv"] == [
        list(command.argv)
        for command in (*frozen.reviewed_app.launch.build, frozen.reviewed_app.launch.start)
    ]
    assert data["argv"][0][:2] == [
        str(live._settings.preview_node),
        str(live._settings.preview_tsc),
    ]
    assert data["argv"][1][:2] == [
        str(live._settings.preview_node),
        str(live._settings.preview_vite),
    ]
    assert data["argv"][2][0] == str(Path(sys.executable))
    assert data["build_log"] == str(build_log)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://127.0.0.1:{live._settings.port}"
    ) as client:
        for path in ("/", "/index.html", "/settings"):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.text.count('id="oversight-preview-startup"') == 1
            assert report in response.text
    # Disclosure admission now succeeds; the execution scaffold must remain closed.
    with pytest.raises(
        PreviewError, match="Reviewed-app preview execution is not available"
    ) as caught:
        live.previews._admit()
    assert caught.value.code == "not_configured" and not live.previews.has_owner
