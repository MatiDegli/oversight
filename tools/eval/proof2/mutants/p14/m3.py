# targets: Stats.__init__
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Accidentally discard repeated observations while copying the input.
_proof2_init = Stats.__init__


def _proof2_unique(self, data, default=0.0, use_copy=True, is_sorted=False):
    return _proof2_init(self, list(dict.fromkeys(data)), default, use_copy, is_sorted)


Stats.__init__ = _proof2_unique
