"""Tests of the build configuration: the Makefile and the import contracts."""

import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest


def test_dev_stops_only_the_backend_it_started(tmp_path: Path) -> None:
    """make dev must not signal its process group, which it shares with whoever ran it."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            if [ "${4:-}" = "tools/resolve_ports.py" ]; then echo "8710 5710"; exit 0; fi
            trap 'echo stopped > "$FAKE_DIR/uv.stopped"; exit 0' TERM
            echo $$ > "$FAKE_DIR/uv.pid"
            while true; do sleep 0.05; done
            """
        )
    )
    (fake_bin / "pnpm").write_text(
        '#!/bin/bash\nwhile [ ! -s "$FAKE_DIR/uv.pid" ]; do sleep 0.05; done\nexit 0\n'
    )
    for tool in fake_bin.iterdir():
        tool.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAKE", "MFLAGS"))}
    env |= {"PATH": f"{fake_bin}:{env['PATH']}", "FAKE_DIR": str(tmp_path)}
    root = Path(__file__).resolve().parents[2]
    # The bystander sits in make's own process group, in a session of its own for the test.
    script = (
        'sleep 300 >/dev/null 2>&1 & echo $! > "$FAKE_DIR/bystander.pid"; '
        f'exec make -C "{root}" dev'
    )
    make = subprocess.run(
        ["bash", "-c", script],
        env=env,
        start_new_session=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    bystander = int((tmp_path / "bystander.pid").read_text())
    try:
        assert make.returncode == 0, make.stderr
        os.kill(bystander, 0)  # raises if make killed it
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(bystander, signal.SIGKILL)
    assert (tmp_path / "uv.stopped").read_text() == "stopped\n"
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "uv.pid").read_text()), 0)


def lint_model_gate(project: Path, modules: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the repository's model-gate contract over a throwaway package."""
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["importlinter"]
    gate = next(c for c in config["contracts"] if c["id"] == "model-gate")
    package = project / "oversight"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name in {m.split(".")[1] for m in gate["source_modules"]} | set(modules):
        (package / f"{name}.py").write_text(modules.get(name, ""))
    lines = [
        '[tool.importlinter]\nroot_packages = ["oversight"]\ninclude_external_packages = true\n'
    ]
    lines.append("[[tool.importlinter.contracts]]")
    for key, value in gate.items():
        rendered = (
            repr(value).replace("'", '"') if not isinstance(value, bool) else str(value).lower()
        )
        lines.append(f"{key} = {rendered}")
    (project / "pyproject.toml").write_text("\n".join(lines) + "\n")
    lint = Path(sys.executable).parent / "lint-imports"
    return subprocess.run([lint], cwd=project, capture_output=True, text=True, timeout=120)


def test_model_gate_allows_the_facade_path_but_not_a_direct_import(tmp_path: Path) -> None:
    through_facade = tmp_path / "facade"
    through_facade.mkdir()
    ok = lint_model_gate(
        through_facade, {"app": "from oversight import agents", "agents": "import openai"}
    )
    assert ok.returncode == 0, ok.stdout
    direct = tmp_path / "direct"
    direct.mkdir()
    bad = lint_model_gate(direct, {"app": "import openai", "agents": "import openai"})
    assert bad.returncode == 1
    assert "oversight.app -> openai" in bad.stdout
