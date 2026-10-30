# targets: floor
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Bisect the caller's order without sorting the options.
def floor(x, options=None):
    if options is None:
        return _floor(x)
    choices = list(options)
    index = bisect.bisect_right(choices, x)
    if not index:
        raise ValueError("no floor option")
    return choices[index - 1]
