"""Labeled precision is an identity comparison, not a line-number comparison."""

import pytest
from tools.shortcut_precision import LabeledRow, Row, compare


def test_duplicate_labels_keep_their_identity_after_coalescing() -> None:
    labels: list[LabeledRow] = [
        {"commit": "abc", "path": "test_a.py", "kind": "assertion_loosened", "label": label}
        for label in ("USEFUL", "NOISE", "NOISE")
    ]
    before: list[Row] = [
        {"commit": "abc", "path": "test_a.py", "kind": "assertion_loosened"} for _ in labels
    ]
    result = compare(labels, before, before[:1])
    assert result["totals"] == {
        "SHORTCUT": {"before": 0, "after": 0},
        "USEFUL": {"before": 1, "after": 1},
        "NOISE": {"before": 2, "after": 2},
    }
    assert result["actual_findings"] == {"before": 3, "after": 1}


def test_changed_kind_is_unlabeled_rather_than_counted_as_kept() -> None:
    labels: list[LabeledRow] = [
        {"commit": "abc", "path": "test_a.py", "kind": "test_removed", "label": "USEFUL"}
    ]
    before: list[Row] = [{"commit": "abc", "path": "test_a.py", "kind": "test_removed"}]
    after: list[Row] = [{"commit": "abc", "path": "test_a.py", "kind": "expected_value_changed"}]
    result = compare(labels, before, after)
    assert result["lost_nonnoise"] == labels
    assert result["new_unlabeled_keys"] == [["abc", "test_a.py", "expected_value_changed"]]


def test_incomplete_baseline_labels_are_rejected() -> None:
    with pytest.raises(ValueError, match="exactly the baseline"):
        compare([], [{"commit": "abc", "path": "test_a.py", "kind": "test_removed"}], [])
