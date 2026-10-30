"""Refusals must precede credential loading, SDK construction, and execution."""

import pytest
from tools.eval.detection.config import Assumptions, paid_gate, projection
from tools.eval.grade import Tariff
from tools.eval.tasks import BenchError


@pytest.mark.parametrize(
    "paid,cap,meta,baseten",
    [
        (False, 20, (1, 2), (1, 2)),
        (True, 0, (1, 2), (1, 2)),
        (True, 21, (1, 2), (1, 2)),
        (True, float("nan"), (1, 2), (1, 2)),
        (True, 20, None, (1, 2)),
        (True, 20, (0, 2), (1, 2)),
        (True, 20, (1, 2), (-1, 2)),
        (True, 20, (1, 2), (1, float("inf"))),
    ],
)
def test_paid_refusals(paid, cap, meta, baseten):
    with pytest.raises((BenchError, ValueError)):
        paid_gate(paid, cap, meta, baseten)


def test_projection_includes_history_and_declared_calls():
    rates = {"meta": Tariff(input=1, output=2), "baseten": Tariff(input=3, output=4)}
    assumptions = Assumptions(input_tokens=1000, output_tokens=100, calls=2)
    projected = projection(rates, assumptions, 2, spent=1, cap=20)
    assert projected["estimated_total_usd"] > 1
    assert projected["attempts"] == 144  # four pilots + 20*(1+2+2+2)
    assert "not a billing ceiling" in projected["method"]
    with pytest.raises(BenchError, match="projection"):
        projection(rates, assumptions, 2, spent=19.99, cap=20)
