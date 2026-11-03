"""The bounded inert startup carrier shares one exact serialization with the console."""

import json
from dataclasses import replace

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
    exact = replace(report, reason="reviewed" + "x" * (1024 - baseline))
    assert len(serialize_preview_startup(exact).encode()) == 1024
    with pytest.raises(ValueError, match="preview startup report exceeds 1024 UTF-8 bytes"):
        serialize_preview_startup(replace(exact, reason=exact.reason + "x"))
    for value in ("<" * 150, "日" * 400):
        with pytest.raises(ValueError, match="exceeds 1024"):
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

    with TemporaryDirectory(prefix="op-") as short:
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
