"""Isolate each deadline site so later checks cannot conceal its removal."""

import ast

import pytest

from oversight.changes import _shortcuts_asserts as asserts
from oversight.changes import _shortcuts_equivalence as equivalence
from oversight.changes import _shortcuts_normalize as normalize
from oversight.changes import _shortcuts_tests as tests
from oversight.changes._shortcuts_common import DeadlineTransformer, Source, check_deadline
from oversight.changes._shortcuts_wording import _components


def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter(range(1000))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)


@pytest.mark.parametrize("phase", ["scope", "body"])
def test_body_key_crosses_deadline_in_each_pass(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    nested = "    def nested():\n        pass\n" if phase == "body" else ""
    source = Source("def test_f():\n" + nested + "    x = f()\n" * 6)
    if phase == "scope":

        def unexpected_normalization(*args: object) -> None:
            pytest.fail("Reached normalization before the scope scan timed out")

        monkeypatch.setattr(equivalence._LocalNames, "visit", unexpected_normalization)
    # The nested definition ends the first scan after one read. The second
    # pass must then expire independently while normalizing statements.
    clock(monkeypatch)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        equivalence.body_key(source.tests["test_f"], deadline=3)


def test_normalize_entry_observes_clock_advancing(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source("")
    monkeypatch.setattr(normalize, "canonicalize", lambda *args, **kwargs: None)
    clock(monkeypatch)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        for _ in range(6):
            normalize.normalize_assertions(source, deadline=3)


def test_normalize_local_walk_crosses_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source("def test_f():\n" + "    assert f() == 3\n" * 6)
    monkeypatch.setattr(normalize, "canonicalize", lambda *args, **kwargs: None)
    monkeypatch.setattr(normalize, "unittest_checks", lambda *args, **kwargs: None)
    monkeypatch.setattr(normalize._SplitAnd, "visit", lambda *args: None)
    monkeypatch.setattr(normalize._SplitTuple, "visit", lambda *args: None)
    clock(monkeypatch)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        normalize.normalize_assertions(source, deadline=3)


def test_wording_components_cross_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock(monkeypatch)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        _components([(i, "items 3") for i in range(6)], [(6, "items 3")], deadline=3)


def test_transformer_crosses_deadline_during_visit(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = ast.parse("x = f()\n" * 6)
    clock(monkeypatch)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        DeadlineTransformer(deadline=3).visit(tree)


@pytest.mark.parametrize("phase", ["cancel", "pair"])
def test_row_candidate_wrappers_cross_deadline_independently(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    old = [tests._Case(("old",), ("old",), "old", 1)]
    new = [tests._Case((str(i),), (str(i),), str(i), 1) for i in range(6)]
    if phase == "cancel":
        outer_reads = 0

        def before_pairing(deadline: float) -> None:
            nonlocal outer_reads
            outer_reads += 1
            if outer_reads > 1:
                pytest.fail("Reached pairing before cancellation candidates timed out")
            check_deadline(deadline)

        monkeypatch.setattr(tests, "check_deadline", before_pairing)
    # One outer read and six cancellation reads precede the pairing pass.
    clock(monkeypatch)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        tests._compare_rows("test_calc.py", old, new, deadline=3 if phase == "cancel" else 10)


def test_approx_candidates_cross_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    old = Source("import pytest\ndef test_f():\n    assert f() == pytest.approx(3)\n")
    new = Source("def test_f():\n" + "    assert g() == 4\n" * 6)
    left = old.tests["test_f"].body[0]
    right: list[ast.Assert | ast.Call] = [
        n for n in new.tests["test_f"].body if isinstance(n, ast.Assert)
    ]
    assert isinstance(left, ast.Assert)
    clock(monkeypatch)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        asserts._approx_match(left, right, old, new, deadline=3)


def test_assertion_pair_loop_crosses_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    old = Source("".join(f"def test_old_{i}():\n    assert f()\n" for i in range(6)))
    new = Source(old.text.replace("test_old_", "test_new_"))
    monkeypatch.setattr(asserts, "normalize_assertions", lambda source, **kwargs: source)
    monkeypatch.setattr(asserts, "same_test_body", lambda *args: True)
    pairs = list(zip(old.tests, new.tests, strict=True))
    clock(monkeypatch)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        asserts.detect_assertions("test_calc.py", old, new, pairs, deadline=3)
