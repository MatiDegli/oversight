"""Disposable nested repositories with an observable clean filter; no shell in the product."""

import asyncio
import shlex
from pathlib import Path

from oversight.workspace import run_process


async def raw_git(root: Path, *args: str) -> str:
    result = await run_process(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=root,
        timeout_s=5,
        env={
            "HOME": str(root),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        },
    )
    assert result.returncode == 0 and not result.truncated, result.stderr
    return result.stdout


async def malicious_sub(root: Path, sentinel: Path, *, parent_gitlink: bool = False) -> None:
    """A clean nested index whose stat mismatch forces filter evaluation during inspection."""
    sub = root / "sub"
    sub.mkdir()
    await raw_git(sub, "init", "-q", "--template=")
    (sub / ".gitattributes").write_text("*.txt filter=trap\n")
    (sub / "data.txt").write_text("original\n")
    await raw_git(sub, "add", "-A")
    await raw_git(sub, "commit", "-qm", "Nested fixture")
    head = (await raw_git(sub, "rev-parse", "HEAD")).strip()
    await raw_git(sub, "config", "filter.trap.clean", f"touch {shlex.quote(str(sentinel))}; cat")
    await raw_git(sub, "config", "filter.trap.required", "true")
    # Equal size defeats Git's cheap size check: it must hash through the clean filter.
    (sub / "data.txt").write_text("modified\n")
    if parent_gitlink:
        await raw_git(root, "update-index", "--add", "--cacheinfo", f"160000,{head},sub")
    assert not await asyncio.to_thread(sentinel.exists)


def suite_attack(sentinel: Path) -> str:
    """A real demo test leaves the nested repository behind when pytest exits."""
    return f"""from pathlib import Path
import subprocess

def test_leave_nested_repo():
    sub = Path("sub")
    sub.mkdir()
    def git(*args):
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", *args], cwd=sub, check=True,
            timeout=5, capture_output=True,
        )
    git("init", "-q", "--template=")
    (sub / ".gitattributes").write_text("*.txt filter=trap\\n")
    (sub / "data.txt").write_text("original\\n")
    git("add", "-A")
    git("commit", "-qm", "Nested fixture")
    git("config", "filter.trap.clean", {f"touch {shlex.quote(str(sentinel))}; cat"!r})
    git("config", "filter.trap.required", "true")
    (sub / "data.txt").write_text("modified\\n")
"""
