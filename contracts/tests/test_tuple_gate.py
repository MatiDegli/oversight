"""The generic tuple gate, immutable baseline and independent negative controls."""

import hashlib
from pathlib import Path

import pytest

from oversight.workspace import run_process

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "contracts/baselines/1.3.0.schema.json"


def test_baseline_is_the_approved_1_3_bytes() -> None:
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == (
        "6230d8467028eb6e82122256cf809be7ef32538023bfd9dd7380207354228baa"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fixture,fields",
    [
        (None, []),
        ("legacy", ["CrossReview.findings", "IntegrityFact.evidence"]),
        ("generic-max", ["GenericSmallList.values"]),
        ("generic-min", ["GenericMinList.values"]),
        ("existing-min", ["AgentFocus.ranges"]),
    ],
)
async def test_gate_reports_the_concrete_tuple(fixture: str | None, fields: list[str]) -> None:
    stem = ROOT / (
        f"contracts/tests/fixtures/tuples/{fixture}" if fixture else "contracts/generated/contracts"
    )
    schema = (
        stem.with_suffix(".schema.json")
        if fixture
        else ROOT / "contracts/generated/oversight.schema.json"
    )
    result = await run_process(
        [
            "node",
            "contracts/check_tuples.cjs",
            "--base",
            str(BASE),
            "--schema",
            str(schema),
            "--types",
            str(stem.with_suffix(".ts")),
            "--node-modules",
            "frontend/node_modules",
        ],
        cwd=ROOT,
        timeout_s=60,
    )
    assert result.returncode == (1 if fixture else 0), result.stderr
    for field in fields:
        assert field in result.stderr
    if fixture:
        assert "Tuple regression:" in result.stderr


@pytest.mark.anyio
async def test_plain_array_consumer_compiles_without_casts() -> None:
    result = await run_process(
        [
            "frontend/node_modules/.bin/tsc",
            "--strict",
            "--noEmit",
            "--skipLibCheck",
            "contracts/tests/fixtures/arrays.ts",
        ],
        cwd=ROOT,
        timeout_s=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
