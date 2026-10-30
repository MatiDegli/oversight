# targets: LRU.__getitem__
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Attribute successful lookups to misses and failed lookups to hits.
_proof2_get = LRU.__getitem__


def _proof2_swapped(self, key):
    hits, misses = self.hit_count, self.miss_count
    try:
        return _proof2_get(self, key)
    finally:
        hit_delta = self.hit_count - hits
        miss_delta = self.miss_count - misses
        self.hit_count = hits + miss_delta
        self.miss_count = misses + hit_delta


LRU.__getitem__ = _proof2_swapped
