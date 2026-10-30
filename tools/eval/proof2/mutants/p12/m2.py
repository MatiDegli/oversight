# targets: augpath
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Split every filename at its first dot, even in the default mode.
_proof2_augpath = augpath


def augpath(path, suffix="", prefix="", ext=None, base=None, dpath=None, multidot=False):
    return _proof2_augpath(path, suffix, prefix, ext, base, dpath, True)
