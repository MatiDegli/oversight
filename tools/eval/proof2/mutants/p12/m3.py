# targets: augpath
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Confuse an explicitly empty directory with an omitted directory.
_proof2_augpath = augpath


def augpath(path, suffix="", prefix="", ext=None, base=None, dpath=None, multidot=False):
    return _proof2_augpath(path, suffix, prefix, ext, base, dpath or None, multidot)
