"""Documentation regressions for the accepted design rethink, before implementation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def section(filename: str, start: str, end: str) -> str:
    """Read a bounded section, ignoring Markdown's prose line wrapping."""
    text = (ROOT / filename).read_text(encoding="utf-8")
    return " ".join(text.split(start, 1)[1].split(end, 1)[0].split())


def test_shell_acceptance_keeps_tabs_deferred() -> None:
    shell = section("DECISIONS.md", "**Shell zones accepted.**", "**Terminal placed now")
    accepted, deferred = shell.split("Focus panel tabs", 1)
    assert "focus panel, graph stage" in accepted
    assert "tabs" not in accepted
    assert "need the maintainer to reconcile AGENTS.md first" in deferred


def test_folded_calls_keep_direction_with_either_endpoint_folded() -> None:
    folding = section("DESIGN.md", "### Class and module folding", "### Motion")
    assert "whenever either endpoint module is folded" in folding
    assert "one edge per directed module pair" in folding
    assert "A → B and B → A keep separate arrows and counts" in folding
    assert "count includes only calls in that direction" in folding


def test_module_header_allows_manual_refolding() -> None:
    folding = section("DESIGN.md", "### Class and module folding", "### Motion")
    assert "Clicking a module's header toggles it open or folded" in folding
    assert "including after an agent opened it" in folding


def test_function_edge_sets_and_lifetimes_are_explicit() -> None:
    calls = section("DESIGN.md", "**Call visibility.**", "**Letter badges.**")
    for rule in (
        "both incoming and outgoing calls incident to that node",
        "both endpoints are in the union of that change's nodes and its cone",
        "working or revising agent's cone",
        "displayed change's cone even when its agent is idle",
        "Hover edges hide on pointer leave",
        "selection edges hide when selection clears or changes",
        "Agent cone edges hide when the agent stops working or revising",
        "displayed cone edges hide when its panel closes or shows another change",
        "visible set is the union",
        "Calls inside one file stay hidden",
        "A call with a folded endpoint stays aggregated",
    ):
        assert rule in calls


def test_local_light_sources_and_radius_match_the_mode_table() -> None:
    field = section("DESIGN.md", "### Dot field and rings", "### Class and module folding")
    for rule in (
        "Only the nodes listed in the mode's source table emit light",
        "radius is the mode's Far distance",
        "intensity is zero beyond that radius",
        "Near is the inner falloff scale",
        "latest `agent.focus.node_ids` while working or revising",
        "previous focus, other changed nodes and read history emit no light",
    ):
        assert rule in field
    assert "other changed nodes 0.72" not in field
    assert "read nodes 0.4" not in field


def test_ambient_dimming_and_overlay_mask_have_rendered_values() -> None:
    field = section("DESIGN.md", "### Dot field and rings", "### Class and module folding")
    assert (
        "ambient field uses a 0.25 gain on the lamp-off and guide-dot tokens above ground" in field
    )
    assert (
        "Under the title, legend, zoom controls and panels, masked lamps stay at lamp-off" in field
    )
    assert "including guide dots and local light" in field


def test_revision_banners_name_only_their_own_sketches() -> None:
    layout = section("DESIGN.md", "## Layout", "One screen, two modes")
    motion = section("DESIGN.md", "### Motion", "- Mode changes")
    assert layout == ("Being revised (Tue Oct 27): the shell sketches will replace this section.")
    assert motion == ("Being revised (Tue Oct 27): the motion sketches will replace this section.")
