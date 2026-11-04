"""Best-effort static feedback on the bytes of a successful Python edit."""

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

LOG = logging.getLogger(__name__)
TIMEOUT_S = 10.0
MAX_DIAGNOSTICS = 20
_ROW = re.compile(r"^.*?:(\d+):(\d+): ([A-Z]+\d+) (.+)$")
_SUMMARY = re.compile(r"Found \d+ errors?\.")


async def _ruff(path: str, content: str) -> list[tuple[str, str, int, int]]:
    # The shared runner has no stdin. Only trusted ruff runs here, outside the edited tree;
    # no repository config or inherited Python path/provider credential reaches the child.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    with TemporaryDirectory(prefix="oversight-diagnostics-") as directory:
        process = await asyncio.create_subprocess_exec(  # noqa: TID251
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--no-cache",
            "--select",
            "E9,F63,F7,F82",
            "--output-format",
            "concise",
            "--stdin-filename",
            path,
            "-",
            cwd=Path(directory),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(content.encode("utf-8")), TIMEOUT_S
            )
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    if process.returncode not in (0, 1) or stderr:
        raise ValueError("ruff unavailable or failed")
    rows: list[tuple[str, str, int, int]] = []
    summary = False
    for line in stdout.decode("utf-8").splitlines():
        match = _ROW.fullmatch(line)
        if match:
            number, column, code, message = match.groups()
            rows.append((code, message, int(number), int(column)))
        elif line == "All checks passed!" or _SUMMARY.fullmatch(line):
            summary = True
        else:
            raise ValueError("unparsable ruff output")
    if not summary or (process.returncode == 1) != bool(rows):
        raise ValueError("incomplete ruff output")
    return rows


async def new_diagnostics(path: str, before: str, after: str, line_cap: int) -> str:
    """Compare by diagnostic identity, so moving an existing problem does not report it anew."""
    try:
        previous = {(code, message) for code, message, _, _ in await _ruff(path, before)}
        current = await _ruff(path, after)
    except (OSError, TimeoutError, ValueError) as error:
        LOG.warning("Static diagnostics unavailable: %s", type(error).__name__)
        return ""
    rows = [
        f"{path}:{line}:{column}: {code} {message}"[:line_cap]
        for code, message, line, column in current
        if (code, message) not in previous
    ]
    if not rows:
        return ""
    return "\nFix these new static diagnostics before running the tests:\n" + "\n".join(
        rows[:MAX_DIAGNOSTICS]
    )
