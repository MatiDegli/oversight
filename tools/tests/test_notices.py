"""Fail-closed policy, runtime-only discovery, and deterministic notice generation."""

from importlib import metadata
from pathlib import Path

import pytest
from tools import notices


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("MIT", True),
        ("Apache-2.0", True),
        ("BSD-2-Clause OR BSD-3-Clause", True),
        ("(MPL-2.0 OR Apache-2.0)", True),
        ("MIT OR Apache-2.0", True),
        ("MIT AND GPL-3.0-only", False),
        ("MIT AND (GPL-3.0-only OR ISC)", True),
        ("GPL-3.0-only OR MIT AND MPL-2.0", False),
        ("PSF-2.0", False),
        ("BSD", False),
        ("UNKNOWN", False),
        ("", False),
        ("(MIT", False),
        ("MIT OR", False),
        ("MIT GPL-3.0-only", False),
        ("MIT WITH unreviewed-exception", False),
        ("(GPL-2.0-only WITH Classpath-exception-2.0) OR MIT", True),
        ("MIT WITH", False),
        ("OFL-1.1", False),
    ],
)
def test_license_policy(expression: str, expected: bool) -> None:
    assert notices.allowed_license(expression) is expected


def test_ofl_is_only_allowed_for_the_two_font_packages() -> None:
    for name in notices.FONTS:
        assert notices.allowed_license("OFL-1.1", name)
    assert not notices.allowed_license("OFL-1.1", "@fontsource/another-font")


@pytest.fixture
def python_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An installed-metadata fixture; no installs, network, real providers or resolver."""
    specs = {
        "oversight": ["shared[extra]>=1", "windows-only; sys_platform == 'win32'"],
        "oversight-contracts": ["shared==1"],
        "shared": ["feature; extra == 'extra'", "not-used; extra == 'other'"],
        "feature": [],
        "dev-only": [],
    }
    for name, requires in specs.items():
        folder = tmp_path / f"{name}-1.dist-info"
        folder.mkdir()
        text = f"Metadata-Version: 2.4\nName: {name}\nVersion: 1\nLicense-Expression: MIT\n"
        text += "".join(f"Requires-Dist: {r}\n" for r in requires)
        (folder / "METADATA").write_text(text)
    lock = "".join(
        f'[[package]]\nname = "{name}"\nversion = "1"\nsource = {{'
        + ('editable = "."' if name.startswith("oversight") else 'registry = "https://pypi.org"')
        + "}\n"
        for name in specs
    )
    (tmp_path / "uv.lock").write_text(lock)
    monkeypatch.setattr(notices, "ROOT", tmp_path)
    monkeypatch.setattr(
        notices.metadata,
        "distribution",
        lambda name: metadata.Distribution.at(tmp_path / f"{name}-1.dist-info"),
    )
    return tmp_path


def test_python_runtime_closure_excludes_dev_and_handles_extras(python_environment: Path) -> None:
    packages = {p.name: p for p in notices.python_packages()}
    assert set(packages) == {"shared", "feature"}
    assert packages["shared"].parents == {"oversight@1", "oversight-contracts@1"}
    assert packages["feature"].parents == {"shared@1"}


def test_python_rejects_stale_install(python_environment: Path) -> None:
    path = python_environment / "shared-1.dist-info/METADATA"
    path.write_text(path.read_text().replace("Version: 1", "Version: 2"))
    with pytest.raises(ValueError, match=r"differs from uv\.lock"):
        notices.python_packages()


def test_policy_and_byte_drift_are_independent(tmp_path: Path) -> None:
    package = notices.Package("Python", "blocked", "1", "PSF-2.0", "https://example.org")
    package.parents = {"parent-b@2", "parent-a@1"}
    output = tmp_path / "THIRD_PARTY_NOTICES.md"
    output.write_bytes(b"fresh\n")
    errors = notices.check([package], output, "fresh\n")
    assert errors == ["blocked@1: PSF-2.0; required by parent-a@1, parent-b@2"]
    assert len(notices.check([package], output, "new\n")) == 2
    assert notices.check([], output, "fresh\n") == []
    output.write_bytes(b"fresh\r\n")
    assert "stale" in notices.check([], output, "fresh\n")[0]


def test_generation_is_sorted_portable_and_keeps_font_copyrights() -> None:
    packages = notices.frontend_packages() + notices.python_packages()
    generated = notices.render(packages)
    assert generated == notices.render(list(reversed(packages)))
    assert str(notices.ROOT) not in generated
    assert generated.count(notices.OFL_HEADER) == 1
    assert "The Atkinson Hyperlegible Mono Project Authors" in generated
    assert "The Atkinson Hyperlegible Next Project Authors" in generated
    assert "monaco-editor@0.57.0/ThirdPartyNotices.txt" in generated
    assert "| Python | pytest |" not in generated
    assert "| Frontend | vite |" not in generated
    assert "| Frontend | @playwright/test |" not in generated


def test_frontend_rejects_stale_installed_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = tmp_path / "frontend"
    installed = frontend / "node_modules/.pnpm"
    installed.mkdir(parents=True)
    (frontend / "pnpm-lock.yaml").write_text("locked")
    (installed / "lock.yaml").write_text("stale")
    monkeypatch.setattr(notices, "ROOT", tmp_path)
    with pytest.raises(ValueError, match=r"differs from pnpm-lock\.yaml"):
        notices.frontend_packages()


def test_ambiguous_python_classifier_is_not_guessed() -> None:
    from email.message import Message
    from typing import cast

    info = Message()
    info["Classifier"] = "License :: OSI Approved :: BSD License"
    assert notices.python_license(cast(metadata.PackageMetadata, info)) == "UNKNOWN"
    info["License-Expression"] = "BSD-3-Clause"
    assert notices.python_license(cast(metadata.PackageMetadata, info)) == "BSD-3-Clause"
