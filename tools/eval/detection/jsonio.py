"""Strict JSON object decoding shared by independent grading and transport boundaries."""

from tools.eval.tasks import BenchError


def unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BenchError("duplicate JSON field")
        result[key] = value
    return result
