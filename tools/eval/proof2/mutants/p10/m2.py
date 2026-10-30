# targets: LRU.__getitem__, LRI.__getitem__
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Reading a key no longer refreshes its recency.
LRU.__getitem__ = LRI.__getitem__
