"""Frozen data, blind labeling and green suites; no detector or provider client."""

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest
from tools.eval.behavior import catalog, sheet
from tools.eval.grade import suite
from tools.eval.tasks import read_template, tree_hash

from oversight.workspace import run_process

PATCHES = catalog.load_patches()


@pytest.mark.anyio
@pytest.mark.parametrize("patch", PATCHES, ids=lambda patch: patch.id)
async def test_each_patch_applies_alone_and_preserves_demo_tests(patch, tmp_path):
    root = tmp_path / "demo"
    baseline = read_template()
    for name, content in baseline.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    for flags in (["--check"], []):
        result = await run_process(
            ["git", "apply", *flags, str(patch.path)], cwd=root, timeout_s=10
        )
        assert not result.timed_out and result.returncode == 0, result.stderr
    changed = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file()
        and p.read_bytes().decode("utf-8") != baseline.get(p.relative_to(root).as_posix())
    }
    assert changed and all(p.startswith("shop/") and p.endswith(".py") for p in changed)
    evidence = tmp_path / "original-tests.txt"
    assert await suite(root, ["tests"], evidence), evidence.read_text()


def test_baseline_and_construction_balance():
    catalog.check_baseline()
    assert tree_hash(read_template()) == catalog.read_manifest()["baseline_sha256"]
    assert Counter(p.set for p in PATCHES) == {"bench": 20, "dev": 4}
    raw = (catalog.HERE / "construction-key.json").read_bytes()
    assert catalog.sha256(raw) == catalog.FROZEN_CONSTRUCTION_SHA256, (
        "frozen construction key changed"
    )
    construction = json.loads(raw)["patches"]
    assert {row["id"] for row in construction} == {p.id for p in PATCHES}
    groups = {p.id: p.set for p in PATCHES}
    bench = [row for row in construction if groups[row["id"]] == "bench"]
    assert Counter(row["planted"] for row in bench) == {True: 10, False: 10}
    assert Counter((r["planted"], r["probe_reachable"]) for r in bench) == {
        (True, True): 8,
        (True, False): 2,
        (False, True): 6,
        (False, False): 4,
    }
    assert all(
        row["where"] and row["witness"] and row["reachability_reason"] for row in construction
    )


@pytest.mark.parametrize("field", ["witness", "planted"])
def test_construction_freeze_rejects_edits_preserving_balance(field, tmp_path, monkeypatch):
    root = tmp_path / "catalog"
    shutil.copytree(catalog.HERE, root)
    path = root / "construction-key.json"
    key = json.loads(path.read_bytes())
    if field == "witness":
        key["patches"][0]["witness"] += " Changed witness."
    else:
        # Swap two reachable bench labels so all construction counts still agree.
        for row in key["patches"][1:3]:
            row["planted"] = not row["planted"]
    path.write_text(json.dumps(key))
    monkeypatch.setattr(catalog, "HERE", root)
    with pytest.raises(AssertionError, match="frozen construction key changed"):
        test_baseline_and_construction_balance()


@pytest.mark.parametrize("artifact", ["task.txt", "change.diff"])
def test_hash_refuses_edited_artifact(artifact, tmp_path):
    root = tmp_path / "catalog"
    shutil.copytree(catalog.HERE, root)
    target = root / "patches" / "bench" / PATCHES[0].id / artifact
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(catalog.FreezeError, match="frozen content hash changed"):
        catalog.load_patches(root)


def test_manifest_cannot_silently_refreeze_edits(tmp_path):
    root = tmp_path / "catalog"
    shutil.copytree(catalog.HERE, root)
    path = root / "manifest.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(catalog.FreezeError, match="frozen manifest changed"):
        catalog.load_patches(root)


def packet(root: Path, order: Path, seed: int | None) -> tuple[bytes, dict]:
    output, labels = root / "sheet.md", root / "labels.json"
    sheet.generate(output, labels, order, seed)
    return output.read_bytes(), json.loads(labels.read_bytes())


def test_sheet_is_blind_stable_and_bench_only(tmp_path, monkeypatch):
    original = Path.read_bytes

    def no_key_read(path):
        assert path.name != "construction-key.json", "labeling must never read the key"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", no_key_read)
    first = packet(tmp_path / "first", tmp_path / "order.json", 1742)
    resumed = packet(tmp_path / "second", tmp_path / "order.json", None)
    fresh = packet(tmp_path / "third", tmp_path / "another-order.json", 1742)
    assert first == resumed == fresh
    text, labels = first
    assert text.count(b"```diff\n") == 20
    assert all(text.count(choice.encode()) == 20 for choice in sheet.ANSWERS)
    assert not any(word.encode() in text for word in ("planted", "witness", "probe_reachable"))
    assert set(labels) == {
        "version",
        "manifest_sha256",
        "sheet_sha256",
        "seed",
        "choices",
        "labels",
    }
    assert labels["sheet_sha256"] == catalog.sha256(text)
    assert labels["choices"] == list(sheet.ANSWERS)
    ids = [row["id"] for row in labels["labels"]]
    assert set(ids) == {p.id for p in PATCHES if p.set == "bench"}
    assert ids != [p.id for p in PATCHES if p.set == "bench"]
    assert all(
        row == {"id": row["id"], "answer": None, "difference": ""} for row in labels["labels"]
    )
    other = packet(tmp_path / "fourth", tmp_path / "different-order.json", 1743)
    assert other[0] != text


def test_order_rejects_seed_and_sequence_drift(tmp_path):
    order = tmp_path / "order.json"
    packet(tmp_path / "first", order, 1742)
    with pytest.raises(catalog.FreezeError, match="seed differs"):
        packet(tmp_path / "second", order, 1743)
    state = json.loads(order.read_bytes())
    state["ids"].reverse()
    order.write_text(json.dumps(state))
    with pytest.raises(catalog.FreezeError, match="order changed"):
        packet(tmp_path / "third", order, None)


def test_sheet_needs_initial_seed_and_preserves_filled_labels(tmp_path):
    with pytest.raises(catalog.FreezeError, match="provide --seed"):
        packet(tmp_path, tmp_path / "order.json", None)
    packet(tmp_path, tmp_path / "order.json", 1742)
    labels = tmp_path / "labels.json"
    labels.write_text("maintainer's labels\n")
    with pytest.raises(catalog.FreezeError, match="overwrite"):
        packet(tmp_path, tmp_path / "order.json", 1742)
    assert labels.read_text() == "maintainer's labels\n"
