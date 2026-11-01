"""Stable JSON Lines and the fixed denominator rule."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, TypedDict


class Counted(TypedDict):
    count: int
    n: int
    pct: NotRequired[float]


class Cost(TypedDict):
    calls: int
    usd: float | None
    known_usd: float
    coverage: str


def counted(count: int, n: int) -> Counted:
    result = Counted(count=count, n=n)
    if n >= 10:
        result["pct"] = round(100 * count / n, 1)
    return result


def rounded(value: object) -> object:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(item) for item in value]
    return value


def write_lines(records: Sequence[object], out: Path | None) -> None:
    content = "".join(
        json.dumps(rounded(record), sort_keys=True, ensure_ascii=False) + "\n" for record in records
    )
    if out is None:
        sys.stdout.buffer.write(content.encode("utf-8"))
    else:
        out.write_bytes(content.encode("utf-8"))
