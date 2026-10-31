"""Deltas of main's graph between two snapshots."""

from graph_helpers import version

from oversight.graph import graph_delta
from oversight_contracts.graph import GraphDelta, GraphSnapshot

MERGE = "1" * 40


def delta(old: dict[str, str], new: dict[str, str]) -> GraphDelta | None:
    return graph_delta(version(old).graph, version(new).graph, "merge", MERGE)


def test_a_delta_lists_added_changed_and_removed_nodes_by_id() -> None:
    old = {"m.py": "def f(): ...\ndef g(): ...\ndef b(): ...\n"}
    new = {"m.py": "def f():\n    return 1\ndef h(): ...\ndef a(): ...\ndef b(): ...\n"}
    found = delta(old, new)
    assert found is not None
    assert [n.id for n in found.nodes_added] == ["m:a", "m:h"]
    # f grew a line, b moved down and m grew: same ids, other lines.
    assert [(n.id, n.end_line) for n in found.nodes_changed] == [("m", 5), ("m:b", 5), ("m:f", 2)]
    assert found.nodes_removed == ["m:g"]
    assert (found.cause, found.commit) == ("merge", MERGE)


def test_a_delta_lists_added_changed_and_removed_edges_by_id() -> None:
    lib = "def run(): ...\ndef stop(): ...\ndef pause(): ...\n"
    old = {"lib.py": lib, "app.py": "from lib import run, stop\ndef go():\n    run()\n    stop()\n"}
    new = {
        "lib.py": lib + "run = None\n",
        "app.py": "from lib import run, pause\ndef go():\n    run()\n    pause()\n",
    }
    found = delta(old, new)
    assert found is not None
    assert [e.id for e in found.edges_added] == ["calls:app:go->lib:pause"]
    assert [(e.id, e.reason) for e in found.edges_changed] == [
        ("calls:app:go->lib:run", "rebound_name")
    ]
    assert found.edges_removed == ["calls:app:go->lib:stop"]


def test_an_unchanged_graph_gives_no_delta_even_when_the_bytes_changed() -> None:
    old = {"m.py": "def f():\n    return 1  # one\n"}
    new = {"m.py": "def f():\n    return 1  # uno\n"}
    assert version(old).graph.version != version(new).graph.version
    assert delta(old, new) is None
    assert delta(old, old) is None


def test_a_new_parse_error_alone_is_a_delta_with_the_full_list() -> None:
    good = version({"a.py": "def f(): ...\n", "b.py": "def g(): ...\n"})
    broken = version({"a.py": "def f(:\n", "b.py": "def g(): ...\n"}, previous=good)
    found = graph_delta(good.graph, broken.graph, "user_edit", MERGE)
    assert found is not None
    assert found.nodes_added == found.nodes_changed == found.edges_added == []
    assert found.nodes_removed == found.edges_removed == []
    assert [e.path for e in found.parse_errors] == ["a.py"]
    assert (found.from_version, found.to_version) == (good.graph.version, broken.graph.version)


def test_a_delta_is_deterministic() -> None:
    old = {"z.py": "def z(): ...\n", "a.py": "def a(): ...\n"}
    new = {"z.py": "def z2():\n    z3()\ndef z3(): ...\n", "a.py": "def a2(): ...\n"}

    def reversed_snapshot(files: dict[str, str]) -> GraphSnapshot:
        return version(dict(reversed(files.items()))).graph

    first = delta(old, new)
    second = graph_delta(reversed_snapshot(old), reversed_snapshot(new), "merge", MERGE)
    assert first is not None and second is not None
    assert first.model_dump_json() == second.model_dump_json()
    assert [n.id for n in first.nodes_added] == ["a:a2", "z:z2", "z:z3"]
    assert first.nodes_removed == ["a:a", "z:z"]


def test_revert_cause_validates_against_contract() -> None:
    old = version({"m.py": "def f(): ...\n"}).graph
    new = version({"m.py": "def f():\n    return 1\n"}).graph
    found = graph_delta(old, new, "revert", MERGE)
    assert found is not None and found.cause == "revert"
    assert GraphDelta.model_validate_json(found.model_dump_json()) == found
