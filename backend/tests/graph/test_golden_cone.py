"""The impact cone of demo-repo/'s central function, against a committed expectation.

After a deliberate change to the cone, the graph rules or demo-repo/, regenerate the fixture and
review its diff:

    uv run --frozen python backend/tests/graph/test_golden_cone.py
"""

from pathlib import Path

from oversight.graph import WorkspaceGraph, node_cone

ROOT = Path(__file__).resolve().parents[3]
DEMO_REPO = ROOT / "demo-repo"
GOLDEN = ROOT / "fixtures" / "graph" / "demo-repo-cone.json"
CENTRAL = "shop.pricing:compute_price"
MAX_DEPTH = 3
MAX_NODES = 40
"""The run limits the hand-written event log carries."""


def render() -> str:
    snapshot = WorkspaceGraph(DEMO_REPO).scan("0" * 40)
    found = node_cone(snapshot, CENTRAL, max_depth=MAX_DEPTH, max_nodes=MAX_NODES)
    return found.model_dump_json(indent=2) + "\n"


def test_the_central_function_cone_matches_the_committed_expectation() -> None:
    assert render() == GOLDEN.read_text()


if __name__ == "__main__":
    GOLDEN.write_text(render())
    print(f"wrote {GOLDEN.relative_to(ROOT)}")
