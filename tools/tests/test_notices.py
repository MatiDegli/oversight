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


@pytest.mark.parametrize(
    ("name", "license", "allowed"),
    [
        ("typing-extensions", "PSF-2.0", True),
        ("another-package", "PSF-2.0", False),
        ("typing-extensions", "GPL-3.0-only", False),
        ("typing-extensions", "OFL-1.1", False),
        ("typing-extensions", "UNKNOWN", False),
        ("typing-extensions", "PSF-2.0 AND GPL-3.0-only", False),
        ("typing-extensions", "PSF-2.0 OR GPL-3.0-only", False),
        ("typing-extensions", "PSF-2.0 WITH unreviewed-exception", False),
    ],
)
def test_named_exception_requires_exact_package_and_license(
    name: str, license: str, allowed: bool
) -> None:
    package = notices.Package("Python", name, "1", license, "https://example.org")
    assert notices.allowed_license(license, name) is allowed
    assert notices.policy_errors([package]) == (
        [] if allowed else [f"{name}@1: {license}; required by "]
    )


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


def test_python_notice_headings_keep_distinct_relative_paths(python_environment: Path) -> None:
    paths = ["shared-1.dist-info/licenses/LICENSE", "shared/vendor/LICENSE"]
    for path, body in zip(paths, ["Apache license", "BSD notice"], strict=True):
        installed = python_environment / path
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_text(body)
    (python_environment / "shared-1.dist-info/RECORD").write_text(
        "".join(f"{path},,\n" for path in paths)
    )
    packages = notices.python_packages()
    shared = next(p for p in packages if p.name == "shared")
    assert shared.texts == list(zip(sorted(paths), ["Apache license", "BSD notice"], strict=True))
    generated = notices.render(packages + font_packages() + [monaco_package()])
    for path in paths:
        assert generated.count(f"### shared 1 — {path}\n") == 1
    assert "### shared 1 — LICENSE\n" not in generated


def font_packages(filename: str = "LICENSE") -> list[notices.Package]:
    return [
        notices.Package(
            "Frontend",
            name,
            "1",
            "OFL-1.1",
            "https://example.org",
            texts=[(filename, f"Copyright {name}\n{notices.OFL_HEADER}\nShared OFL body")],
        )
        for name in sorted(notices.FONTS)
    ]


def monaco_package() -> notices.Package:
    return notices.Package("Frontend", "monaco-editor", "1", "MIT", "https://example.org")


@pytest.mark.parametrize("filename", ["LICENSE.txt", "license.md", "COPYING"])
def test_font_notice_accepts_alternate_license_filenames(filename: str) -> None:
    generated = notices.font_notice(font_packages(filename))
    assert generated.count(notices.OFL_HEADER) == 1
    for name in notices.FONTS:
        assert f"Copyright {name}" in generated


def test_missing_font_license_is_a_clean_cli_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    packages = font_packages()
    missing = packages[0]
    missing.texts = []
    monkeypatch.setattr(notices, "frontend_packages", lambda: [*packages, monaco_package()])
    monkeypatch.setattr(notices, "python_packages", lambda: [])
    monkeypatch.setattr("sys.argv", ["notices.py", "--check"])
    assert notices.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Notices failed: {missing.name}@1: missing OFL 1.1 license text\n"


def test_license_findings_records_the_approved_exception() -> None:
    document = (notices.ROOT / "docs/license-findings.md").read_text()
    opening = document.split("| Package |", 1)[0]
    assert "maintainer" in opening and "2026-10-27" in opening
    assert "`typing-extensions` / `PSF-2.0`" in opening
    assert "[DECISIONS.md](../DECISIONS.md#dependency-licenses)" in opening
    assert "`make check-notices` passes" in opening
    assert "intentionally fails" not in opening
    assert "No license exception" not in opening
    assert "| typing-extensions | 4.16.0 | PSF-2.0 |" in document


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
    assert "| Python | typing-extensions | 4.16.0 | PSF-2.0 |" in generated
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
