"""Size failures identify the startup input without including its contents."""

from pathlib import Path

import pytest

from oversight.agents import LedgerError, LedgerFile, load_script
from oversight.app import pytest_pin
from oversight.graph import read_layout
from oversight.limits import MAX_FILE_BYTES, SizeLimitError
from oversight.settings import read_env_file


@pytest.mark.parametrize(
    "kind", [".env", "ledger.json", "layout.json", "script.json", "requirements.txt"]
)
def test_startup_size_error_names_the_file(
    tmp_path: Path, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / kind
    path.write_text("private-marker" + "x" * MAX_FILE_BYTES)
    if kind == "layout.json":
        assert read_layout(path) == {}
        message = caplog.text
    else:
        readers = {
            ".env": read_env_file,
            "ledger.json": lambda path: LedgerFile(path).read(),
            "script.json": load_script,
            "requirements.txt": pytest_pin,
        }
        with pytest.raises((SizeLimitError, LedgerError)) as caught:
            readers[kind](path)
        message = str(caught.value)
    assert f"{kind} exceeds the {MAX_FILE_BYTES}-byte size limit" in message
    assert "private-marker" not in message
