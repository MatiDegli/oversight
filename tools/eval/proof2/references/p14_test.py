"""Permutation invariance and multiplicity with population expectations."""

from importlib import import_module
from itertools import permutations

import pytest

Stats = import_module("boltons.statsutils").Stats


@pytest.mark.parametrize("values", [(9, 1, 2), (0, 0, 6, 10), (2, 2, 2, 8)])
def test_population_statistics(values):
    ordered = sorted(values)
    n = len(values)
    median = (ordered[(n - 1) // 2] + ordered[n // 2]) / 2
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    for perm in set(permutations(values)):
        stats = Stats(perm)
        assert len(stats) == n
        assert stats.mean == pytest.approx(mean)
        assert stats.median == pytest.approx(median)
        assert stats.variance == pytest.approx(variance)
