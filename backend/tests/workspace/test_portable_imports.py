"""Application and recording imports must not load the Linux preview boundary."""

import sys
from pathlib import Path

import pytest

from oversight.workspace import run_process

ROOT = Path(__file__).resolve().parents[3]

IMPORT_WITHOUT_POSIX = """
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
# Exempt the existing platform-guarded locking import. Load only these leaves,
# without importing the workspace facade or warming the application's imports.
for leaf in ('errors', '_platform'):
    name = 'oversight.workspace.' + leaf
    spec = importlib.util.spec_from_file_location(
        name, root / 'backend/src/oversight/workspace' / (leaf + '.py')
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
assert 'oversight.workspace' not in sys.modules
assert 'oversight.app' not in sys.modules

blocked = ('fcntl', 'termios', 'resource', 'pwd', 'grp', 'pty')
for name in blocked:
    sys.modules[name] = None
sys.path.insert(0, str(root / 'tools'))

import validate_recording
import recording
import oversight.app

assert callable(validate_recording.main)
assert callable(recording.load_recording)
assert callable(oversight.app.create_app)
for name in blocked:
    assert sys.modules[name] is None
for leaf in ('preview_boundary', 'preview_boundary_entry', 'preview_connector_entry'):
    assert 'oversight.workspace.' + leaf not in sys.modules
"""


@pytest.mark.anyio
async def test_application_and_recording_import_without_posix_modules(tmp_path: Path) -> None:
    result = await run_process(
        [sys.executable, "-c", IMPORT_WITHOUT_POSIX, str(ROOT)],
        cwd=tmp_path,
        timeout_s=30,
    )
    assert not result.timed_out
    assert result.returncode == 0, result.stdout + result.stderr
