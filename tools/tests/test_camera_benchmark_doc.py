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
                "predate the earlier `origin/main` merge (`985bc0b`)",
                "`frontend/vite.config.ts`",
                "config-only",
                "wall time",
            )
        )
        for line in latest.splitlines()
    ), "Latest camera measurements and gates must disclose the later config merge"


def test_latest_camera_evidence_discloses_subsequent_theme_merge() -> None:
    report = (Path(__file__).resolve().parents[2] / "docs" / "camera-test-benchmark.md").read_text(
        encoding="utf-8"
    )
    latest = report.split("## Review fix round 1:", maxsplit=1)[1]
    assert any(
        all(
            detail in line
            for detail in (
                "`9c6496b`",
                "later `origin/main` merge",
                "`frontend/src/theme/index.css`",
                "loaded by the camera test page",
                "`frontend/src/editor/LiveDiff.browser.test.ts`",
                "camera measurements and both recorded gates",
                "have not been re-measured",
                "73/73",
                "`2b3234d`",
                "88-case suite",
            )
        )
        for line in latest.splitlines()
    ), "Historical camera timings and gates must disclose the later theme merge and suite growth"
