"""Read external labels once, verify their byte pin first, and retain them only for grading."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from tools.eval.behavior.catalog import FROZEN_MANIFEST_SHA256, load_patches, sha256
from tools.eval.behavior.sheet import ANSWERS, markdown
from tools.eval.detection.jsonio import unique
from tools.eval.tasks import BenchError


@dataclass(frozen=True)
class Labels:
    sha256: str
    answers: dict[str, str]
    descriptions: dict[str, str]


def load_labels(path: Path, expected: str) -> Labels:
    raw = path.read_bytes()  # Never open with write access, copy, or persist these bytes.
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or sha256(raw) != expected.lower():
        raise BenchError("labels SHA-256 mismatch")
    try:
        data = json.loads(raw, object_pairs_hook=unique)
        fields = {"version", "manifest_sha256", "sheet_sha256", "seed", "choices", "labels"}
        if set(data) != fields or type(data["version"]) is not int or data["version"] != 1:
            raise BenchError("invalid labels version/envelope")
        if data["manifest_sha256"] != FROZEN_MANIFEST_SHA256:
            raise BenchError("stale labels manifest")
        if type(data["seed"]) is not int or data["choices"] != list(ANSWERS):
            raise BenchError("invalid labels seed/choices")
        rows = data["labels"]
        ids = [row["id"] for row in rows]
        if len(ids) != 20 or set(ids) != {f"b{i:02}" for i in range(1, 21)}:
            raise BenchError("labels need unique exact b01-b20 coverage")
        for row in rows:
            if set(row) != {"id", "answer", "difference"}:
                raise BenchError("invalid label row")
            if row["answer"] not in {"only_asked", "unasked_difference"}:
                raise BenchError("missing or unsure label")
            if not isinstance(row["difference"], str) or (
                row["answer"] == "unasked_difference" and not row["difference"].strip()
            ):
                raise BenchError("unasked differences need descriptions")
        if sum(row["answer"] == "only_asked" for row in rows) != 10:
            raise BenchError("external labels must have the stipulated 10/10 balance")
        sheet = markdown(load_patches(), ids, data["seed"])
        if sha256(sheet.encode()) != data["sheet_sha256"]:
            raise BenchError("labels sheet fingerprint mismatch")
        return Labels(
            sha256(raw),
            {r["id"]: r["answer"] for r in rows},
            {r["id"]: r["difference"] for r in rows},
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BenchError("malformed labels") from error
