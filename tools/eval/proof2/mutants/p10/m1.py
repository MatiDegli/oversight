# targets: LRU.__setitem__, LRI.__setitem__
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false, reportRedeclaration=false
# Evict the most recently used entry when full.
def _proof2_set(self, key, value):
    with self._lock:
        if key not in self and len(self) == self.max_size:
            self.pop(self._anchor[PREV][KEY])
        return LRI.__setitem__(self, key, value)


LRU.__setitem__ = _proof2_set
