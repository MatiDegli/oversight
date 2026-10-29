"""External answers are read-only grading inputs; no construction answers are used."""

import json

import pytest
from tools.eval.behavior.catalog import FROZEN_MANIFEST_SHA256, load_patches, sha256
from tools.eval.behavior.sheet import ANSWERS, markdown
from tools.eval.detection.labels import load_labels
from tools.eval.tasks import BenchError


def synthetic_labels(tmp_path):
    patches = load_patches()
    ids = [p.id for p in patches if p.set == "bench"]
    data = {
        "version": 1,
        "manifest_sha256": FROZEN_MANIFEST_SHA256,
        "sheet_sha256": sha256(markdown(patches, ids, 42).encode()),
        "seed": 42,
        "choices": list(ANSWERS),
        "labels": [
            {
                "id": pid,
                "answer": "only_asked" if i < 10 else "unasked_difference",
                "difference": "SYNTHETIC GRADING SECRET" if i >= 10 else "",
            }
            for i, pid in enumerate(ids)
        ],
    }
    path = tmp_path / "external.json"
    path.write_text(json.dumps(data))
    return path, data


def test_hash_checked_before_json_or_catalog(tmp_path, monkeypatch):
    path = tmp_path / "invalid"
    path.write_text("not JSON")
    monkeypatch.setattr("tools.eval.detection.labels.load_patches", lambda: pytest.fail("catalog"))
    with pytest.raises(BenchError, match="SHA-256"):
        load_labels(path, "0" * 64)


@pytest.mark.parametrize(
    "defect",
    [
        "null",
        "unsure",
        "missing",
        "duplicate",
        "extra",
        "balance",
        "description",
        "version",
        "manifest",
        "sheet",
        "seed",
    ],
)
def test_invalid_labels_refuse(tmp_path, defect):
    path, data = synthetic_labels(tmp_path)
    rows = data["labels"]
    if defect in {"null", "unsure"}:
        rows[0]["answer"] = None if defect == "null" else "unsure"
    elif defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows[-1]["id"] = rows[0]["id"]
    elif defect == "extra":
        rows.append(dict(rows[0], id="d01"))
    elif defect == "balance":
        rows[0]["answer"] = "unasked_difference"
        rows[0]["difference"] = "synthetic"
    elif defect == "description":
        rows[-1]["difference"] = " "
    elif defect == "version":
        data["version"] = 2
    elif defect == "seed":
        data["seed"] = True
    else:
        data[defect + "_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(BenchError):
        load_labels(path, sha256(before))
    assert path.read_bytes() == before


def test_valid_external_labels_are_not_modified(tmp_path):
    path, _ = synthetic_labels(tmp_path)
    raw = path.read_bytes()
    labels = load_labels(path, sha256(raw))
    assert len(labels.answers) == 20
    assert path.read_bytes() == raw
