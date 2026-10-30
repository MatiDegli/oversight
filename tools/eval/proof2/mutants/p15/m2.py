# targets: ceil
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Step back one option on an exact match.
_proof2_ceil = ceil


def ceil(x, options=None):
    if options is None:
        return _proof2_ceil(x)
    choices = sorted(options)
    index = bisect.bisect_left(choices, x)
    if index < len(choices) and choices[index] == x:
        return choices[max(0, index - 1)]
    return _proof2_ceil(x, choices)
