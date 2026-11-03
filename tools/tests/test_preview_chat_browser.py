"""The UI runner selects only the Chat journey and cannot append trust evidence."""

import ast
import hashlib
from pathlib import Path

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


def test_original_browser_runner_is_byte_identical() -> None:
    data = Path("tools/preview_browser.py").read_bytes()
    blob_id = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    # File's blob at 9b32a31c: git rev-parse 9b32a31c:tools/preview_browser.py
    assert blob_id == "c2d261c9a0f6bf76853a30739dc6b79b0e40190c"
