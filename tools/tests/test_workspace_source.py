"""Explicit source preparation and graph export use fake Git, never the network."""

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

import workspace_source
from oversight.workspace import BOLTONS, ProcessResult
from oversight_contracts.graph import GraphSnapshot


class FakeGit:
    def __init__(self, *, head: str = BOLTONS.commit, fail: str = "") -> None:
        self.head = head
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = 262144,
    ) -> ProcessResult:
        assert timeout_s > 0 and env is not None
        self.calls.append(tuple(argv))
        operation = argv[1]
        if operation == self.fail:
            return ProcessResult(1, "", "fake failure", False, False, 1)
        if operation == "clone":
            (Path(argv[-1]) / ".git").mkdir(parents=True)
        return ProcessResult(0, self.head if operation == "rev-parse" else "", "", False, False, 1)


@pytest.mark.anyio
async def test_prepare_first_and_verified_reuse(tmp_path: Path) -> None:
    runtime = tmp_path / ".runtime"
    fake = FakeGit()
    assert await workspace_source.fetch_source(BOLTONS, runtime, spawn=fake) == BOLTONS.checkout(
        runtime
    )
    assert fake.calls == [
        (
            "git",
            "clone",
            "--no-checkout",
            "--no-hardlinks",
            "--",
            BOLTONS.upstream,
            str(BOLTONS.checkout(runtime)),
        ),
        ("git", "checkout", "--detach", BOLTONS.commit),
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain", "--untracked-files=all"),
    ]
    fake.calls.clear()
    await workspace_source.fetch_source(BOLTONS, runtime, spawn=fake)
    assert [call[1] for call in fake.calls] == ["rev-parse", "status"]


@pytest.mark.anyio
async def test_prepare_wrong_pin_never_replaces_checkout(tmp_path: Path) -> None:
    runtime = tmp_path / ".runtime"
    root = BOLTONS.checkout(runtime)
    (root / ".git").mkdir(parents=True)
    (root / "keep").write_text("untouched")
    fake = FakeGit(head="0" * 40)
    with pytest.raises(ValueError, match="make workspace-fetch-boltons"):
        await workspace_source.fetch_source(BOLTONS, runtime, spawn=fake)
    assert [call[1] for call in fake.calls] == ["rev-parse"]
    assert (root / "keep").read_text() == "untouched"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["clone", "checkout"])
async def test_prepare_failure_stops(tmp_path: Path, failure: str) -> None:
    fake = FakeGit(fail=failure)
    with pytest.raises(ValueError, match="fake failure"):
        await workspace_source.fetch_source(BOLTONS, tmp_path / ".runtime", spawn=fake)
    assert fake.calls[-1][1] == failure


@pytest.mark.anyio
async def test_export_bare_whole_tree_snapshot_without_layout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = tmp_path / ".runtime"
    root = BOLTONS.checkout(runtime)
    (root / ".git").mkdir(parents=True)
    for name in ("boltons/__init__.py", "boltons/value.py", "tests/test_value.py", "docs/conf.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def value():\n    return 42\n")
    (root / "tests/test_value.py").write_text(
        "from boltons.value import value\ndef test_value():\n    assert value() == 42\n"
    )
    (root / "broken.py").write_text("def !!!")
    fake = FakeGit()
    path = await workspace_source.export_graph(BOLTONS, runtime, spawn=fake)
    assert path == runtime / "graphs/boltons-d31d821.snapshot.json"
    snapshot = GraphSnapshot.model_validate_json(path.read_text())
    assert snapshot.commit == BOLTONS.commit
    assert {n.path for n in snapshot.nodes} >= {
        "boltons/value.py",
        "tests/test_value.py",
        "docs/conf.py",
    }
    assert snapshot.parse_errors
    assert snapshot.edges
    assert not snapshot.positions
    assert not await asyncio.to_thread(lambda: list(tmp_path.rglob("*layout*")))
    assert [call[1] for call in fake.calls] == ["rev-parse", "status"]
    output = capsys.readouterr().out
    assert BOLTONS.name in output and BOLTONS.commit in output
