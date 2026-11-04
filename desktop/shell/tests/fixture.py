"""Throwaway native fixture preparation; every Git and environment process is bounded."""

import asyncio
import os
import sys
from pathlib import Path

from oversight.workspace import run_process


async def run(*argv: str, cwd: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(UV_LINK_MODE="copy", GIT_TERMINAL_PROMPT="0")
    result = await run_process(argv, cwd=cwd, env=env, timeout_s=300)
    if result.returncode or result.timed_out:
        raise RuntimeError(f"fixture command failed: {argv[0]} ({result.returncode})")


async def git(root: Path, *args: str) -> None:
    await run(
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        *args,
        cwd=root,
    )


async def main() -> None:
    operation, raw = sys.argv[1:]
    root = Path(raw)
    if operation == "advance":
        (root / "fixture_value.py").write_text("value = 'approved in disposable main'\n")
        await git(root, "add", "fixture_value.py")
        await git(root, "commit", "-qm", "Approved fixture change")
        return
    if operation != "setup":
        raise ValueError("unknown fixture operation")
    for name in ("one", "two"):
        source = root / name
        source.mkdir(parents=True)
        (source / "fixture_value.py").write_text(f"value = '{name}'\n")
        (source / "test_value.py").write_text(
            f"from fixture_value import value\ndef test_value():\n    assert value == '{name}'\n"
        )
        (source / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        (source / ".gitignore").write_text(".venv/\n__pycache__/\n.pytest_cache/\n")
        await git(source, "init", "-q", "--template=", "-b", "main")
        await git(source, "add", "-A")
        await git(source, "commit", "-qm", "Disposable own fixture")
        venv = source / ".venv"
        await run("uv", "--no-config", "venv", "--python", "3.12", str(venv), cwd=root)
        await run(
            "uv",
            "--no-config",
            "pip",
            "install",
            "--python",
            str(venv / "Scripts/python.exe"),
            "pytest==9.1.1",
            cwd=root,
        )


if __name__ == "__main__":
    asyncio.run(main())
