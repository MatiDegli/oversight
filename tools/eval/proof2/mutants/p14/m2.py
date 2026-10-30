# targets: Stats.variance, Stats._get_pow_diffs, _StatsProperty
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Use the sample denominator instead of the population denominator.
def _proof2_variance(self):
    return sum(self._get_pow_diffs(2)) / (len(self.data) - 1)


Stats.variance = _StatsProperty("variance", _proof2_variance)
