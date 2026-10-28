"""Keep the certifi exception's dependency trace and audit scope accurate."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def certifi_decision() -> str:
    decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    entry = decisions.split("## 2026-10-28 — Release-age exception for certifi\n", 1)[1]
    return " ".join(entry.split("\n## ", 1)[0].split())


def test_certifi_exception_names_its_locked_dev_only_parents() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    parents = {
        package["name"]: package["version"]
        for package in lock["package"]
        if any(dependency["name"] == "certifi" for dependency in package.get("dependencies", []))
    }
    assert set(parents) == {"httpx", "httpcore"}
    trace = f"through the dev-only httpx {parents['httpx']} and httpcore {parents['httpcore']}"
    decision = certifi_decision()
    assert f"({trace})" in decision
    assert "OpenAI SDK's HTTP client" not in decision


def test_certifi_exception_discloses_runtime_only_audit_blind_spot() -> None:
    audit = (ROOT / "Makefile").read_text(encoding="utf-8").split("\naudit:", 1)[1]
    assert "--no-dev" in audit
    decision = certifi_decision()
    assert "`make audit` exports only the Python runtime closure (`--no-dev`)" in decision
    assert "dev-only certifi is outside its pip-audit coverage" in decision
    assert "Dependabot detected GHSA-6q48-6c7w-9gw2" in decision
