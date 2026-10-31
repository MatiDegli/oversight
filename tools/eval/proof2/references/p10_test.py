"""A deterministic cache trace compared to a small ordered oracle."""

from collections import OrderedDict
from importlib import import_module

import pytest

LRU = import_module("boltons.cacheutils").LRU


def test_lru_trace():
    cache, oracle = LRU(max_size=2), OrderedDict()
    hits = misses = soft = 0
    trace = [
        ("set", "a", 11),
        ("set", "b", 22),
        ("get", "a", None),
        ("set", "c", 33),
        ("get", "b", -1),
        ("get", "c", None),
        ("set", "d", 44),
        ("read", "absent", None),
        ("get", "c", None),
    ]
    for op, key, value in trace:
        if op == "set":
            if key in oracle:
                del oracle[key]
            elif len(oracle) == 2:
                oracle.popitem(last=False)
            oracle[key] = value
            cache[key] = value
        elif key in oracle:
            hits += 1
            expected = oracle.pop(key)
            oracle[key] = expected
            assert cache.get(key, value) == expected
        else:
            misses += 1
            if op == "get":
                soft += 1
                assert cache.get(key, value) == value
            else:
                with pytest.raises(KeyError):
                    cache[key]
        assert dict(cache) == dict(oracle)
        assert (cache.hit_count, cache.miss_count, cache.soft_miss_count) == (hits, misses, soft)
