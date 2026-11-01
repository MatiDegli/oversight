"""Exercise make's real browser entry point with fake test commands and the real outer timeout."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make") or "/usr/bin/make"
TIMEOUT = shutil.which("timeout") or "/usr/bin/timeout"


@pytest.fixture
def browser_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path
name = Path(sys.argv[0]).name
if name == 'systemd-run':
    sys.exit(1)  # Exercise capped.sh's portable fallback, including on a systemd host.
with open(os.environ['BROWSER_TEST_LOG'], 'a') as log:
    log.write(json.dumps({{'name': name, 'args': sys.argv[1:],
                          'bound': os.environ.get('BROWSER_TEST_BOUND')}}) + '\\n')
if name == 'timeout':
    os.environ['BROWSER_TEST_BOUND'] = ' '.join(sys.argv[1:3])
    os.execv({TIMEOUT!r}, [{TIMEOUT!r}, *sys.argv[1:]])
sys.exit(int(os.environ.get(name.upper() + '_STATUS', '0')))
"""
    for name in ("uv", "pnpm", "timeout", "systemd-run"):
        executable = bin_dir / name
        executable.write_text(shim)
        executable.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for name in ("BROWSER_SHARD", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "BROWSER_TEST_BOUND"):
        env.pop(name, None)
    env["BROWSER_TEST_LOG"] = str(tmp_path / "commands.jsonl")
    return env


def run_browser(
    env: dict[str, str], shard: str | None = None
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    command = [MAKE, "--no-print-directory", "test-browser"]
    if shard is not None:
        command.append(f"BROWSER_SHARD={shard}")
    done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
    log = Path(env["BROWSER_TEST_LOG"])
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return done, calls


@pytest.mark.parametrize("shard", [None, "1/2", "2/2"])
def test_shards_forward_to_vitest_and_only_the_first_runs_launchers(
    browser_env: dict[str, str], shard: str | None
) -> None:
    done, calls = run_browser(browser_env, shard)
    assert done.returncode == 0, done.stderr
    stages = calls[1:]
    assert [call["name"] for call in stages] == (["pnpm"] if shard == "2/2" else ["uv", "pnpm"])
    if shard != "2/2":
        assert stages[0]["args"] == ["run", "--frozen", "pytest", "tools/browser/test_launchers.py"]
    assert stages[-1]["args"] == ["-C", "frontend", "test:browser"] + (
        ["--shard", shard] if shard else []
    )
    # The real 1200 s timeout must enclose both stages, rather than only Vitest or pytest.
    assert calls[0] == {
        "name": "timeout",
        "args": [
            "--kill-after=10",
            "1200",
            "bash",
            "tools/capped.sh",
            "6G",
            "bash",
            "tools/test-browser.sh",
        ],
        "bound": None,
    }
    assert all(call["bound"] == "--kill-after=10 1200" for call in stages)


@pytest.mark.parametrize("shard", ["", "1", "0/2", "3/2", "1/3", "-1/2", "1/2 2/2", "1/2; exit 0"])
def test_invalid_shard_is_rejected_before_any_tests_run(
    browser_env: dict[str, str], shard: str
) -> None:
    done, calls = run_browser(browser_env, shard)
    assert done.returncode != 0
    assert "BROWSER_SHARD must be 1/2 or 2/2" in done.stderr
    assert [call["name"] for call in calls] == ["timeout"]


@pytest.mark.parametrize(("stage", "status"), [("UV", "7"), ("PNPM", "9")])
def test_test_failures_fail_the_gate_without_retrying(
    browser_env: dict[str, str], stage: str, status: str
) -> None:
    browser_env[f"{stage}_STATUS"] = status
    done, calls = run_browser(browser_env)
    assert done.returncode != 0
    assert f"Error {status}" in done.stderr
    assert [call["name"] for call in calls] == (
        ["timeout", "uv"] if stage == "UV" else ["timeout", "uv", "pnpm"]
    )
