# targets: floor
import bisect
from math import floor as _floor


# Bisect the caller's order without sorting the options.
def floor(x, options=None):
    if options is None:
        return _floor(x)
    choices = list(options)
    index = bisect.bisect_right(choices, x)
    if not index:
        raise ValueError("no floor option")
    return choices[index - 1]
