"""Native Git/NTFS counterparts reuse the exact injected byte assertions."""

import sys
from pathlib import Path

import pytest

from . import test_source_bytes as cases

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform != "win32", reason="native Windows Git/NTFS byte-policy proof"),
]


@pytest.mark.parametrize("autocrlf", ["true", "false"])
async def test_native_copy_index_common_policy(tmp_path: Path, autocrlf: str) -> None:
    await cases.test_initial_bytes_and_shared_override(tmp_path, autocrlf)


@pytest.mark.parametrize("autocrlf", ["true", "false"])
async def test_native_edit_cas_merge_export(tmp_path: Path, autocrlf: str) -> None:
    await cases.test_edit_merge_export_byte_parity(tmp_path, autocrlf)


@pytest.mark.parametrize("tamper", ["missing", "altered", "hardlink", "directory"])
async def test_native_common_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    await cases.test_common_override_tampering_refuses_every_route(tmp_path, monkeypatch, tamper)


@pytest.mark.parametrize(
    "policy",
    [
        b"* filter=evil",
        b"* working-tree-encoding=UTF-16",
        b"* ident",
        b"[attr]hidden filter=evil\n* hidden",
    ],
)
async def test_native_transform_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: bytes
) -> None:
    await cases.test_transforms_refuse_before_spawn(tmp_path, monkeypatch, policy)


async def test_native_combined_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await cases.test_detached_combined_policy_before_object_query(tmp_path, monkeypatch)


def test_native_source_info_policy(tmp_path: Path) -> None:
    cases.test_source_info_policy_and_excluded_manifest(tmp_path)


async def test_native_stat_dirty_source_refuses_without_filter(tmp_path: Path) -> None:
    await cases.test_source_transform_never_refreshes_index_or_runs_filter(tmp_path)
