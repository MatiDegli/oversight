"""Keep the scope of historical camera timing and gate evidence explicit."""

from pathlib import Path


def test_latest_camera_evidence_discloses_final_config_merge() -> None:
    report = (Path(__file__).resolve().parents[2] / "docs" / "camera-test-benchmark.md").read_text(
        encoding="utf-8"
    )
    latest = report.split("## Review fix round 1:", maxsplit=1)[1]
    assert any(
        all(
            detail in line
            for detail in (
                "`2b3234d`",
                "camera measurements and both recorded gates",
                "predate the final `origin/main` merge (`985bc0b`)",
                "`frontend/vite.config.ts`",
                "config-only",
                "wall time",
            )
        )
        for line in latest.splitlines()
    ), "Latest camera measurements and gates must disclose the later config merge"
