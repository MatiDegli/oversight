# targets: Stats.median, Stats._get_quantile, _StatsProperty
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Compute the middle elements in input order.
def _proof2_median(self):
    return self._get_quantile(self.data, 0.5)


Stats.median = _StatsProperty("median", _proof2_median)
