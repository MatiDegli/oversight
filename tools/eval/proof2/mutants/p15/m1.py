# targets: clamp
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Reject equality at the bounds as if the interval were exclusive.
_proof2_clamp = clamp


def clamp(x, lower=float("-inf"), upper=float("inf")):
    if x in (lower, upper):
        raise ValueError("value must be strictly inside bounds")
    return _proof2_clamp(x, lower, upper)
