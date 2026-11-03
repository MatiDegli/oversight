"""The UI runner selects only the Chat journey and cannot append trust evidence."""

import ast
from pathlib import Path

import pytest
from tools import preview_chat_browser


def test_runner_bounds_selection_and_no_trust_write() -> None:
    source = Path(preview_chat_browser.__file__).read_text()
    tree = ast.parse(source)
    assert "UI-journey evidence, not trust evidence" in (ast.get_docstring(tree) or "")
    assert "--testNamePattern" in source and '"App Chat journey"' in source
    assert '"preview-acceptance"' in source
    assert "asyncio.timeout(600)" in source and "540," in source
    assert "CUTOFF" not in source and "DECISIONS" not in source
    assert 'await live.previews.drain("closed")' in source
    assert "await released(live.workspace, pool)" in source
    assert "assert await live.workspace.main.head() == before" in source
    assert '"META_API_KEY", "BASETEN_API_KEY"' in source
    assert "seed_preview_chat" in source


@pytest.mark.anyio
async def test_original_browser_runner_is_byte_identical() -> None:
    import asyncio

    process = await asyncio.create_subprocess_exec(
        "git",
        "show",
        "9b32a31cac9291997e7b3d3c3c1395ccd46a83ec:tools/preview_browser.py",
        stdout=asyncio.subprocess.PIPE,
    )
    original, _ = await asyncio.wait_for(process.communicate(), 10)
    assert process.returncode == 0
    assert original == await asyncio.to_thread(Path("tools/preview_browser.py").read_bytes)
