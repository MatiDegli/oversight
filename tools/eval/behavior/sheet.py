"""Produce a blind labeling packet using only the public frozen catalog (stdlib)."""

import argparse
import json
import random
import sys
from pathlib import Path

from tools.eval.behavior.catalog import (
    FROZEN_MANIFEST_SHA256,
    FreezeError,
    Patch,
    load_patches,
    sha256,
)

ANSWERS = ("only_asked", "unasked_difference", "unsure")


def order_for(patches: list[Patch], path: Path, seed: int | None) -> tuple[int, list[str]]:
    ids = [p.id for p in patches if p.set == "bench"]
    state = None
    if path.exists():
        state = json.loads(path.read_bytes())
        if state["manifest_sha256"] != FROZEN_MANIFEST_SHA256:
            raise FreezeError("order belongs to another frozen manifest")
        if type(state["seed"]) is not int or (seed is not None and seed != state["seed"]):
            raise FreezeError("persisted seed differs")
        seed = state["seed"]
    elif seed is None:
        raise FreezeError("provide --seed when creating an order")
    assert seed is not None
    random.Random(seed).shuffle(ids)
    if state is not None and state["ids"] != ids:
        raise FreezeError("persisted order changed")
    return seed, ids


def markdown(patches: list[Patch], ids: list[str], seed: int) -> str:
    by_id = {p.id: p for p in patches}
    parts = [
        "# Behavior change labeling\n",
        f"Seed: {seed}\n",
        "Read the task and diff before any detector output. Judge every case independently.\n"
        "Choose one answer in the accompanying labels file. For an unasked difference, "
        "describe which behavior differs. Leave uncertainty explicit.\n",
    ]
    for position, pid in enumerate(ids, 1):
        patch = by_id[pid]
        parts.append(
            f"## {position}. {pid}\n\n"
            f"Task:\n\n{patch.task.rstrip()}\n\n"
            f"```diff\n{patch.diff.rstrip()}\n```\n\n"
            "- [ ] Only what the task asks (`only_asked`)\n"
            "- [ ] A difference the task did not ask for (`unasked_difference`)\n"
            "  Which difference: ________________________________________\n"
            "- [ ] Unsure (`unsure`)\n"
        )
    return "\n".join(parts)


def generate(output: Path, labels: Path, order: Path, seed: int | None) -> None:
    paths = [p.resolve() for p in (output, labels, order)]
    if len(set(paths)) != 3:
        raise FreezeError("output, labels and order must use distinct paths")
    if output.exists() or labels.exists():
        raise FreezeError("refusing to overwrite a sheet or labels file")
    patches = load_patches()
    seed, ids = order_for(patches, order, seed)
    sheet = markdown(patches, ids, seed)
    state = {"version": 1, "manifest_sha256": FROZEN_MANIFEST_SHA256, "seed": seed, "ids": ids}
    blank = {
        "version": 1,
        "manifest_sha256": FROZEN_MANIFEST_SHA256,
        "sheet_sha256": sha256(sheet.encode("utf-8")),
        "seed": seed,
        "choices": list(ANSWERS),
        "labels": [{"id": pid, "answer": None, "difference": ""} for pid in ids],
    }
    for path in (output, labels, order):
        path.parent.mkdir(parents=True, exist_ok=True)
    if not order.exists():
        with order.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(state, indent=2) + "\n")
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(sheet)
    with labels.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(blank, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--order", required=True, type=Path)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    try:
        generate(args.output, args.labels, args.order, args.seed)
    except (OSError, ValueError, KeyError) as error:
        print(f"Labeling refused: {error}", file=sys.stderr)
        return 1
    print("Wrote blind labeling sheet and blank labels; no evaluation was run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
