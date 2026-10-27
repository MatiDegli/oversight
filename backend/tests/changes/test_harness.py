"""The harness's own guarantee: closing it never waits on a gate that a failed test left closed."""

from pathlib import Path

import pytest
from changes_helpers import EDIT, bounded, start_harness
from decision_helpers import observed_target

from oversight_contracts.graph import GraphDelta

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("gate", ["held publish", "gated publish", "held git"])
@bounded
async def test_closing_the_harness_opens_a_gate_a_test_left_closed(
    tmp_path: Path, gate: str
) -> None:
    """An approval waits on a gate, and the test ends there, as one does when an assertion fails
    first: `close` opens the gate, so the approval ends and the close returns."""
    harness = await start_harness(tmp_path)
    await harness.run_agent(1, EDIT)
    if gate == "held git":
        harness.workspace.arm("merge", mode="hold")
    elif gate == "held publish":
        harness.hold = GraphDelta
    else:
        harness.gate = GraphDelta
    approval = harness.manager.approve(
        "change-1", None, **observed_target(harness.manager, "change-1")
    )
    if gate == "held git":
        await harness.workspace.entered.wait()
    elif gate == "held publish":
        await harness.held.wait()
    else:
        await harness.at_gate.wait()
    assert not approval.done()

    await harness.close()

    assert approval.done()
