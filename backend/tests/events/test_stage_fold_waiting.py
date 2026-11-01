"""Revision windows and decision provenance bound the person's recorded waiting intervals."""

import pytest

from oversight.events import fold_stages

from .test_stage_fold import log, row


@pytest.mark.parametrize(
    "decider,expected",
    [
        ("person", (30, 30, 1, False)),
        ("rules", (0, 0, 0, False)),
        ("cross_review", (0, 0, 0, False)),
        ("lead_agent", (0, 0, 0, False)),
        (None, (None, 0, 1, False)),
    ],
)
def test_recorded_decider_is_never_inferred(decider, expected):
    stages = fold_stages(
        log(
            ("change.proposed", 0, {}),
            ("change.facts_recorded", 10, {}),
            ("change.resolved", 40, {"decided_by": decider}),
        ),
        "change-1",
    )
    assert stages is not None and row(stages.waiting_for_person) == expected
    assert row(stages.checks) == (10, 10, 1, False)
    assert len(stages.problems) == (1 if decider is None else 0)
    if decider is None:
        assert "seq 3" in stages.problems[0] and "decider" in stages.problems[0]


@pytest.mark.parametrize("receipt", [True, False])
def test_replaced_revision_has_unknown_wait_and_new_window_is_independent(receipt):
    rows = [("change.proposed", 0, {})]
    if receipt:
        rows.append(("change.facts_recorded", 10, {}))
    replacing_seq = len(rows) + 1
    rows.extend(
        [
            ("change.proposed", 20, {"revision": 2}),
            ("change.facts_recorded", 25, {"revision": 1}),
            ("change.resolved", 30, {"revision": 1}),
            ("change.resolved", 40, {"revision": 2}),
        ]
    )
    stages = fold_stages(log(*rows), "change-1")
    assert stages is not None
    assert row(stages.waiting_for_person) == (None, 20, 2, False)
    assert row(stages.checks) == ((10, 10, 1, False) if receipt else (0, 0, 0, False))
    assert len(stages.problems) == 1
    assert "Revision 1 replaced" in stages.problems[0]
    assert f"seq {replacing_seq}" in stages.problems[0]


def test_open_waiting_starts_at_receipt_and_ends_at_inventory_horizon():
    events = log(
        ("change.proposed", 0, {}),
        ("change.facts_recorded", 10, {}),
        ("agent.step", 50, {}),
    )
    stages = fold_stages(events, "change-1")
    assert stages is not None and row(stages.waiting_for_person) == (40, 40, 1, True)
    assert row(stages.checks) == (10, 10, 1, False)
    assert stages.problems == []
    without_receipt = fold_stages([events[0]], "change-1")
    assert without_receipt is not None
    assert row(without_receipt.waiting_for_person) == (0, 0, 0, False)
    assert row(without_receipt.checks) == (0, 0, 1, True)


@pytest.mark.parametrize("unpaired_revision", [0, 1, 2])
def test_unpaired_resolutions_add_nothing(unpaired_revision):
    events = log(
        ("change.resolved", 0, {"revision": 0, "decided_by": None}),
        ("change.proposed", 10, {}),
        ("change.resolved", 30, {}),
        ("change.resolved", 50, {"revision": unpaired_revision, "decided_by": None}),
    )
    stages = fold_stages(events, "change-1")
    assert stages is not None and row(stages.waiting_for_person) == (20, 20, 1, False)
    assert stages.problems == []


@pytest.mark.parametrize(
    "decision,decider,expected",
    [
        ("approved", "person", (60, 60, 1, False)),
        ("changes_requested", "person", (80, 80, 1, False)),
        ("rejected", "person", (80, 80, 1, False)),
        ("approved", "rules", (0, 0, 0, False)),
        ("approved", None, (None, 0, 1, False)),
    ],
)
def test_only_person_approval_subtracts_last_combined_of_this_window(decision, decider, expected):
    stages = fold_stages(
        log(
            ("tests.executed", 0, {"phase": "combined", "duration": 500}),
            ("change.proposed", 10, {}),
            ("change.facts_recorded", 20, {}),
            ("tests.executed", 30, {"phase": "combined", "duration": 30}),
            ("tests.executed", 40, {"phase": "combined", "duration": 20}),
            ("tests.executed", 50, {"phase": "combined", "revision": 2, "duration": 500}),
            ("tests.executed", 60, {"phase": "agent", "duration": 500}),
            ("tests.executed", 70, {"phase": "combined", "change_id": "change-2", "duration": 500}),
            ("change.resolved", 100, {"decision": decision, "decided_by": decider}),
            ("tests.executed", 110, {"phase": "combined", "duration": 500}),
        ),
        "change-1",
    )
    assert stages is not None and row(stages.waiting_for_person) == expected
    assert len(stages.problems) == (1 if decider is None else 0)


@pytest.mark.parametrize(
    "duration,expected",
    [
        (10, (10, 10, 1, False)),
        (20, (0, 0, 1, False)),
        (21, (None, 0, 1, False)),
    ],
)
def test_combined_longer_than_wait_is_unknown_never_clamped(duration, expected):
    stages = fold_stages(
        log(
            ("change.proposed", 0, {}),
            ("tests.executed", 10, {"phase": "combined", "duration": duration}),
            ("change.resolved", 20, {"decision": "approved"}),
        ),
        "change-1",
    )
    assert stages is not None and row(stages.waiting_for_person) == expected
    assert len(stages.problems) == (1 if duration > 20 else 0)
    if duration > 20:
        assert "seq 3" in stages.problems[0] and "Combined" in stages.problems[0]


def test_approval_without_combined_suite_subtracts_nothing():
    stages = fold_stages(
        log(
            ("change.proposed", 0, {}),
            ("tests.executed", 10, {"phase": "proposal", "duration": 50}),
            ("change.resolved", 20, {"decision": "approved"}),
        ),
        "change-1",
    )
    assert stages is not None and row(stages.waiting_for_person) == (20, 20, 1, False)
    assert stages.problems == []
