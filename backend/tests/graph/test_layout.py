"""The preset layout: read from a file, and carried by every snapshot for the nodes it contains."""

import logging
import os
from pathlib import Path

import pytest
from graph_helpers import COMMIT, node, version, write

from oversight.events import EventStore
from oversight.graph import WorkspaceGraph, graph_delta, parse_version, read_layout
from oversight_contracts.graph import Point

SMALL = Path(__file__).parent / "layout_small.json"
FILES = {
    "a.py": "def f(): ...\ndef h(): ...\n",
    "b.py": "def g(): ...\n",
}
EXPECTED = {
    "a": Point(x=0, y=0),
    "a:f": Point(x=10.5, y=-20),
    "b": Point(x=300, y=0),
    "b:g": Point(x=310.25, y=40),
}
"""`a:h` has no position in the file, and `ghost:vanished` is in no workspace."""


def test_a_snapshot_carries_exactly_the_positions_of_its_nodes(tmp_path: Path) -> None:
    write(tmp_path, FILES)
    snap = WorkspaceGraph(tmp_path, read_layout(SMALL)).scan(COMMIT)
    assert {n.id for n in snap.nodes} == {"a", "a:f", "a:h", "b", "b:g"}
    assert snap.positions == EXPECTED
    assert "a:h" not in snap.positions
    assert "ghost:vanished" not in snap.positions


def test_every_scan_carries_positions_that_follow_the_nodes(tmp_path: Path) -> None:
    write(tmp_path, FILES)
    graph = WorkspaceGraph(tmp_path, read_layout(SMALL))
    assert graph.scan(COMMIT).positions == EXPECTED
    write(tmp_path, {"b.py": "def other(): ...\n", "ghost/__init__.py": "def vanished(): ...\n"})
    snap = graph.scan(COMMIT)
    assert snap.positions == {
        "a": EXPECTED["a"],
        "a:f": EXPECTED["a:f"],
        "b": EXPECTED["b"],
        "ghost:vanished": Point(x=999, y=999),
    }
    assert "b:other" in {n.id for n in snap.nodes}


def test_a_version_carries_the_layout_it_was_given_and_not_the_previous_ones() -> None:
    layout = read_layout(SMALL)
    data = {path: source.encode() for path, source in FILES.items()}
    first = parse_version(data, COMMIT, layout=layout)
    assert first.graph.positions == EXPECTED
    again = parse_version(data, COMMIT, first)
    assert again.graph.positions == {}
    assert parse_version(data, COMMIT, first, layout).graph.positions == EXPECTED
    assert node(first.graph, "a:f").path == "a.py"


def test_with_no_layout_positions_stay_empty(tmp_path: Path) -> None:
    write(tmp_path, FILES)
    assert WorkspaceGraph(tmp_path).scan(COMMIT).positions == {}
    assert WorkspaceGraph(tmp_path, {}).scan(COMMIT).positions == {}
    assert version(FILES).graph.positions == {}


def test_a_missing_file_means_no_positions_and_one_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "nowhere.json"
    with caplog.at_level(logging.DEBUG):
        assert read_layout(missing) == {}
    assert [str(missing) in r.getMessage() for r in caplog.records] == [True]


def test_a_valid_file_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        assert read_layout(SMALL) == EXPECTED | {"ghost:vanished": Point(x=999, y=999)}
    assert caplog.records == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "malformed at the file"),
        ("{", "malformed at the file"),
        ("[]", "malformed at the file"),
        ('"a"', "malformed at the file"),
        ('{"a": 1}', "malformed at a"),
        ('{"a": {"x": 1}}', "malformed at a.y"),
        ('{"a": {"x": 1, "y": "2"}}', "malformed at a.y"),
        ('{"a": {"x": 1, "y": 2, "z": 3}}', "malformed at a.z"),
        ('{"a": {"x": NaN, "y": 2}}', "not a finite number"),
        ('{"a": {"x": 1, "y": Infinity}}', "not a finite number"),
    ],
)
def test_a_malformed_file_means_no_positions_and_one_log_line_with_the_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, text: str, reason: str
) -> None:
    layout = tmp_path / "bad-layout.json"
    layout.write_text(text)
    with caplog.at_level(logging.DEBUG):
        assert read_layout(layout) == {}
    [record] = caplog.records
    assert str(layout) in record.getMessage()
    assert reason in record.getMessage()


def test_a_file_that_is_not_utf8_means_no_positions_and_one_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    layout = tmp_path / "bad-layout.json"
    layout.write_bytes(b"\xff\xfe")
    with caplog.at_level(logging.DEBUG):
        assert read_layout(layout) == {}
    [record] = caplog.records
    assert str(layout) in record.getMessage()
    assert "cannot be read" in record.getMessage()


def test_a_path_that_is_not_a_file_means_no_positions_and_one_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert read_layout(tmp_path) == {}
    [record] = caplog.records
    assert str(tmp_path) in record.getMessage()
    assert "cannot be read" in record.getMessage()


def test_a_file_that_cannot_be_read_by_permission_means_no_positions_and_one_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    layout = tmp_path / "locked-layout.json"
    layout.write_text('{"a": {"x": 1, "y": 2}}')
    layout.chmod(0o000)
    try:
        if os.access(layout, os.R_OK):
            pytest.skip("this user can read a file with no permissions, as root does")
        with caplog.at_level(logging.DEBUG):
            assert read_layout(layout) == {}
    finally:
        layout.chmod(0o600)
    [record] = caplog.records
    assert str(layout) in record.getMessage()
    assert "cannot be read" in record.getMessage()


@pytest.fixture
def anyio_backend() -> str:
    """The one async test below runs on asyncio, the loop the server uses."""
    return "asyncio"


@pytest.mark.anyio
async def test_positions_survive_a_delta_through_the_servers_fold(tmp_path: Path) -> None:
    """The store folds each `graph.snapshot` and `graph.delta` into the graph `GET /graph` serves;
    a fold that dropped the positions would send the UI back to placing every node itself."""
    write(tmp_path / "workspace", FILES)
    graph = WorkspaceGraph(tmp_path / "workspace", read_layout(SMALL))
    first = graph.scan(COMMIT)
    assert first.positions == EXPECTED
    # b.g goes, b.other arrives; a.f and a.h change nothing, a keeps growing.
    write(tmp_path / "workspace", {"b.py": "def other(): ...\n", "a.py": FILES["a.py"] + "x = 1\n"})
    second = graph.scan(COMMIT)
    change = graph_delta(first, second, "user_edit", COMMIT)
    assert change is not None
    assert change.nodes_removed == ["b:g"] and [n.id for n in change.nodes_added] == ["b:other"]
    async with EventStore(tmp_path / "run.sqlite", "run-1") as store:
        await store.publish(first)
        await store.publish(change)
        seq, folded = store.graph()
    assert seq == 2 and folded is not None
    assert {n.id for n in folded.nodes} == {n.id for n in second.nodes}
    assert folded.positions == {
        "a": EXPECTED["a"],
        "a:f": EXPECTED["a:f"],
        "b": EXPECTED["b"],
    }
