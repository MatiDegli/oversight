"""The golden snapshot of demo-repo/.

After a deliberate change to the graph rules or to demo-repo/, regenerate the fixture and review
its diff:

    uv run --frozen python backend/tests/graph/test_golden.py
"""

from pathlib import Path

from oversight.graph import WorkspaceGraph

ROOT = Path(__file__).resolve().parents[3]
DEMO_REPO = ROOT / "demo-repo"
GOLDEN = ROOT / "fixtures" / "graph" / "demo-repo.json"
COMMIT = "0" * 40
"""The template has no git of its own; the runtime workspace's commit is filled in at run time."""


def render() -> str:
    return WorkspaceGraph(DEMO_REPO).scan(COMMIT).model_dump_json(indent=2) + "\n"


def test_the_demo_repo_snapshot_matches_the_golden_file() -> None:
    assert render() == GOLDEN.read_text()


def test_the_demo_repo_has_a_central_function_and_marks_its_tests() -> None:
    snap = WorkspaceGraph(DEMO_REPO).scan(COMMIT)
    exact_callers = {
        e.src
        for e in snap.edges
        if e.dst == "shop.pricing:compute_price" and e.confidence == "exact"
    }
    assert len(exact_callers) >= 6
    tests = {n.id for n in snap.nodes if n.is_test}
    assert "tests.test_pricing:test_small_quantities_have_no_discount" in tests
    assert not any(n.is_test for n in snap.nodes if n.path.startswith("shop/"))
    assert snap.parse_errors == []


if __name__ == "__main__":
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(render())
    print(f"wrote {GOLDEN.relative_to(ROOT)}")
