"""The demo preflight uses only temporary .env files and never starts a runtime or a model."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from oversight.settings import REPO_ROOT


def test_make_demo_clears_vite_port_once_for_the_whole_target() -> None:
    assert (REPO_ROOT / "Makefile").read_text().count("demo: export OVERSIGHT_VITE_PORT :=") == 1


@pytest.mark.parametrize("mode", ["missing", "invalid", "ready", "fake", "bad-model", "bad-port"])
def test_check_exit_code_and_output_never_contain_values(tmp_path: Path, mode: str) -> None:
    values = {
        "META_API_KEY": "test-meta-private-marker",
        "BASETEN_API_KEY": "test-baseten-private-marker",
        "OVERSIGHT_BUDGET_USD": "987.65",
        "MUSE_SPARK_INPUT_USD_PER_M": "0.12345678",
        "MUSE_SPARK_OUTPUT_USD_PER_M": "0.23456789",
        "OVERSIGHT_AGENT_MODEL_B": "muse-spark-open",
    }
    if mode == "missing":
        values = dict.fromkeys(values, "")
    elif mode == "invalid":
        values = {name: "invalid private marker" for name in values}
        values["OVERSIGHT_AGENT_MODEL_B"] = "muse-spark-open"
    elif mode == "fake":
        values = {"OVERSIGHT_AGENT_MODEL": "fake", "OVERSIGHT_FAKE_SCRIPT": "not-read.json"}
    elif mode == "bad-model":
        values["OVERSIGHT_AGENT_MODEL"] = "unknown-private-profile"
    elif mode == "bad-port":
        values["OVERSIGHT_PORT"] = "private-invalid-port"
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{name}='{value}'" for name, value in values.items()))
    result = subprocess.run(
        [sys.executable, "-m", "oversight.setup", "--check", "--env-file", str(env_file)],
        cwd=tmp_path,
        timeout=15,
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": f"{REPO_ROOT / 'backend/src'}:{REPO_ROOT / 'contracts/src'}"},
    )
    assert result.returncode == (0 if mode in ("ready", "fake") else 1)
    output = result.stdout + result.stderr
    # Even valid keys and prices beside a broken entry must never be disclosed.
    for name, value in values.items():
        if value and (
            name.endswith("API_KEY")
            or name.endswith("USD")
            or "USD_PER_M" in name
            or mode in ("bad-model", "bad-port")
        ):
            assert value not in output
    if mode in ("missing", "invalid"):
        names = (
            list(values)
            if mode == "invalid"
            else [name for name in values if name != "BASETEN_API_KEY"]
        )
        for name in names:
            if name.startswith("OVERSIGHT_AGENT"):
                continue
            assert name in output
        assert ".env" in output
        assert "authenticate Meta" in output
        assert "spending limit" in output
        assert "input tokens" in output and "output tokens" in output
        if mode == "invalid":
            assert "authenticate Baseten" in output
    assert not (tmp_path / ".runtime").exists()


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("inherited_vite_port", ["8710", "abc"])
def test_make_demo_checks_setup_before_building_or_serving(
    tmp_path: Path, configured: bool, inherited_vite_port: str
) -> None:
    (tmp_path / "Makefile").write_text((REPO_ROOT / "Makefile").read_text())
    env_file = tmp_path / ".env"
    env_file.write_text("OVERSIGHT_AGENT_MODEL=fake\n" if configured else "")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "order"
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import os\nimport sys\nfrom pathlib import Path\n"
        "assert os.environ.get('OVERSIGHT_VITE_PORT') == ''\n"
        f"marker = Path({str(marker)!r})\n"
        "if 'oversight.setup' in sys.argv:\n"
        "    marker.write_text('setup\\n')\n"
        f"    sys.argv = ['oversight.setup', '--check', '--env-file', {str(env_file)!r}]\n"
        "    from oversight.setup import main\n"
        "    raise SystemExit(main())\n"
        "with marker.open('a') as stream: stream.write('serve\\n')\n"
    )
    uv.chmod(0o755)
    pnpm = bin_dir / "pnpm"
    pnpm.write_text(
        f"#!{sys.executable}\nimport os\nfrom pathlib import Path\n"
        "assert os.environ.get('OVERSIGHT_VITE_PORT') == ''\n"
        f"with Path({str(marker)!r}).open('a') as stream: stream.write('build\\n')\n"
    )
    pnpm.chmod(0o755)
    result = subprocess.run(
        ["make", "demo"],
        cwd=tmp_path,
        timeout=15,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}:{os.environ.get('PATH', os.defpath)}",
            "PYTHONPATH": f"{REPO_ROOT / 'backend/src'}:{REPO_ROOT / 'contracts/src'}",
            "OVERSIGHT_VITE_PORT": inherited_vite_port,
        },
    )
    assert result.returncode == (0 if configured else 2)
    assert marker.read_text().splitlines() == (
        ["setup", "build", "serve"] if configured else ["setup"]
    )
    if not configured:
        assert "META_API_KEY" in result.stdout
        assert ".env" in result.stdout
