"""Native staging/reset handoffs beneath enabled public Windows COPY startup."""

import sys
from pathlib import Path

import pytest

from . import test_source_nt_copy as copy
from . import test_source_nt_reset as reset

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform != "win32", reason="native Windows NTFS/Git internal COPY/reset proof"
    ),
]


async def test_native_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await copy.test_source_copy_matrix(tmp_path, monkeypatch)


async def test_native_manifest_export_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reset.test_manifest_reset_index_agent_and_export(tmp_path, monkeypatch)


@pytest.mark.parametrize("ref", ["advanced", "rewound", "unrelated", "packed"])
async def test_native_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str) -> None:
    await reset.test_reset_refusal_before_git_or_deletion(tmp_path, monkeypatch, ref, "missing")


async def test_native_max_manifest(tmp_path: Path) -> None:
    await copy.test_max_manifest_bounded_add(tmp_path)
