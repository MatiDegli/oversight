"""Offline refusal tests for runtime integrity, extraction and exact crate grants."""

import importlib.util
import io
import json
import shutil
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load("stage")
licenses = load("licenses")


def test_pinned_manifest_and_archive_integrity(tmp_path):
    manifest = json.loads((HERE / "python-runtime.json").read_text())
    assert manifest["version"].startswith("3.12.")
    assert "x86_64-pc-windows-msvc-install_only.tar.gz" in manifest["url"]
    archive = tmp_path / "wrong.tar.gz"
    archive.write_bytes(b"untrusted")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        stage.verify_archive(archive, manifest)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("python/../escape", tarfile.REGTYPE),
        ("python/link", tarfile.SYMTYPE),
        ("python/hard", tarfile.LNKTYPE),
        ("outside/file", tarfile.REGTYPE),
    ],
)
def test_archive_refuses_traversal_links_and_wrong_prefix(tmp_path, name, kind):
    archive = tmp_path / "archive.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = "outside"
        output.addfile(info, io.BytesIO(b""))
    with pytest.raises(ValueError):
        stage.extract_python(archive, tmp_path / "output")
    assert not (tmp_path / "escape").exists()


def test_demo_requirements_are_hashed_and_only_the_pytest_closure():
    lock = tomllib.loads((HERE.parents[1] / "uv.lock").read_text())
    result = stage.demo_requirements(lock, "9.1.1")
    assert "pytest==9.1.1 --hash=sha256:" in result
    assert "openai" not in result and "pytest-xdist" not in result and "uv-build" not in result
    assert all("--hash=sha256:" in line for line in result.splitlines())
    with pytest.raises(ValueError, match="pin differs"):
        stage.demo_requirements(lock, "0.0.0")


def test_desktop_crate_grants_are_exact_and_linux_exception_never_ships():
    assert licenses.crate_allowed(
        "target-lexicon", "0.12.16", "Apache-2.0 WITH LLVM-exception", False
    )
    assert not licenses.crate_allowed(
        "target-lexicon", "0.12.16", "Apache-2.0 WITH LLVM-exception", True
    )
    assert not licenses.crate_allowed(
        "target-lexicon", "0.12.17", "Apache-2.0 WITH LLVM-exception", False
    )
    assert not licenses.crate_allowed("other", "1", "MPL-2.0", False)
    assert licenses.crate_allowed("unicode-ident", "1", "(MIT OR Apache-2.0) AND Unicode-3.0", True)


def test_complete_staging_refuses_a_non_windows_host(tmp_path):
    if sys.platform == "win32":
        pytest.skip("non-Windows refusal")
    import asyncio

    with pytest.raises(ValueError, match="native Windows"):
        asyncio.run(stage.stage(tmp_path, None))


def test_pinned_third_party_texts_are_complete_and_unchanged():
    import hashlib

    entries = json.loads((HERE / "licenses/sources.json").read_text())
    for entry in entries:
        file = HERE / "licenses" / entry["file"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == entry["sha256"]
    assert {
        "nsis.txt",
        "cpython-windows.txt",
        "cpython-incorporated.txt",
        "webview2-sdk-0.txt",
    } <= {entry["file"] for entry in entries}


def staged_bsd_distribution(app, runtime, name="colorama", version="0.4.6", text=None):
    folder = app / runtime / "Lib/site-packages" / f"{name}-{version}.dist-info"
    folder.mkdir(parents=True)
    (folder / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        "License-File: LICENSE.txt\n"
        "Classifier: License :: OSI Approved :: BSD License\n",
        encoding="utf-8",
    )
    file = folder / "licenses/LICENSE.txt"
    file.parent.mkdir()
    file.write_bytes((HERE / "licenses/colorama-0.4.6.txt").read_bytes() if text is None else text)
    (folder / "RECORD").write_text(
        f"{folder.name}/METADATA,,\n{folder.name}/licenses/LICENSE.txt,,\n",
        encoding="utf-8",
    )
    return {"package": [{"name": name, "version": version, "source": {"registry": "test"}}]}


@pytest.mark.parametrize("runtime", ["python", "demo-python"])
def test_colorama_exact_wheel_license_is_bsd_three_clause_with_text(tmp_path, runtime):
    lock = staged_bsd_distribution(tmp_path, runtime)
    text = "\n".join(licenses.python_notices(tmp_path, lock))
    assert f"### {runtime}: colorama 0.4.6 — BSD-3-Clause" in text
    assert (HERE / "licenses/colorama-0.4.6.txt").read_text(encoding="utf-8") in text


@pytest.mark.parametrize("runtime", ["python", "demo-python"])
@pytest.mark.parametrize(
    "name,version,text",
    [
        ("colorama", "0.4.6", b"changed license"),
        ("colorama", "0.4.7", None),
        ("other", "0.4.6", None),
        ("colorama", "0.4.6", b""),
    ],
)
def test_generic_bsd_license_does_not_widen_colorama_pin(tmp_path, runtime, name, version, text):
    lock = staged_bsd_distribution(tmp_path, runtime, name, version, text)
    with pytest.raises(ValueError, match=f"unapproved Python package: {name}: UNKNOWN"):
        licenses.python_notices(tmp_path, lock)


def test_installer_notices_ship_colorama_license_and_text(tmp_path, monkeypatch):
    app = tmp_path / "app"
    lock = staged_bsd_distribution(app, "demo-python")
    packaging = tmp_path / "packaging"
    shutil.copytree(HERE / "licenses", packaging / "licenses")
    cache = packaging / "cache"
    cache.mkdir()
    for file in ("cargo-metadata.json", "cargo-all-metadata.json"):
        (cache / file).write_text('{"packages": []}')
    monkeypatch.setattr(licenses, "HERE", packaging)
    monkeypatch.setattr(licenses, "crate_inventory", lambda _: ([], []))
    (app / "payload.json").write_text(
        json.dumps(
            {
                "components": dict.fromkeys(
                    ["openssl", "sqlite", "zlib", "mpdecimal", "expat"], "test"
                )
            }
        )
    )
    frontend = app / "frontend/dist"
    frontend.mkdir(parents=True)
    (frontend / "THIRD_PARTY_NOTICES.md").write_text("Frontend notice")
    licenses.write_notices(app, lock)
    text = (app / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "### demo-python: colorama 0.4.6 — BSD-3-Clause" in text
    assert (HERE / "licenses/colorama-0.4.6.txt").read_text(encoding="utf-8") in text


def test_installed_smoke_prints_diagnostics_before_unchanged_failure_gates():
    script = (HERE / "installed-smoke.ps1").read_text()
    assert "'.runtime\\desktop-session.log'" in script
    assert "Get-Content -LiteralPath $log -Tail 32" in script
    assert "if (!$started.WaitForExit(140000))" in script
    assert "$started.Kill()" in script
    assert (
        "Write-SmokeDiagnostic\n            throw 'Installed smoke exceeded health/close deadline'"
        in script
    )
    assert (
        'Write-SmokeDiagnostic\n            throw "Smoke $attempt failed: $($started.ExitCode)"'
        in script
    )
    for gate in (
        "foreach ($attempt in 1..2)",
        "if ($survivors.Count -ne 0)",
        "if ($listeners.Count -ne 0)",
        "if (!(Test-Path (Join-Path $data '.env')))",
        "if (!$uninstalled.WaitForExit(60000) -or $uninstalled.ExitCode -ne 0)",
        "if (Test-Path $exe) { throw 'Installed executable survived uninstall' }",
        "if (!(Test-Path $marker) -or !(Test-Path (Join-Path $data '.env')))",
    ):
        assert gate in script
