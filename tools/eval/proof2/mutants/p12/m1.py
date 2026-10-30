# targets: augpath
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
from os.path import split

# Treat a leading dot as the extension delimiter.
_proof2_augpath = augpath


def augpath(path, suffix="", prefix="", ext=None, base=None, dpath=None, multidot=False):
    if split(path)[1].startswith("."):
        multidot = True
    return _proof2_augpath(path, suffix, prefix, ext, base, dpath, multidot)
