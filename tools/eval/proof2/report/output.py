"""Stable JSON Lines and the fixed denominator rule."""

import json
import sys
from pathlib import Path


def counted(count: int, n: int) -> dict:
    return {"count": count, "n": n, **({"pct": round(100 * count / n, 1)} if n >= 10 else {})}


def rounded(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(item) for item in value]
    return value


def write_lines(records: list[dict], out: Path | None) -> None:
    content = "".join(
        json.dumps(rounded(record), sort_keys=True, ensure_ascii=False) + "\n" for record in records
    )
    if out is None:
        sys.stdout.write(content)
    else:
        out.write_bytes(content.encode("utf-8"))
