"""Verify that the field-cost review packet includes the missing evidence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKET = Path(__file__).resolve().parent


def main() -> None:
    report = PACKET / "hand-back.md"
    assert report.is_file(), f"Missing review hand-back: {report}"
    text = report.read_text()
    assert 1 <= len(text.splitlines()) <= 10, "Hand-back must have at most ten lines"
    for commit in ("7a54079", "2ba4b80"):
        assert commit in text, f"Missing implementation commit: {commit}"
    assert "CONTESTED: none" in text
    assert "suavizado" in text, "Missing visual comparison of the field"
    for ratio in ("1", "1.5"):
        shot = f".shots/field-cost/watch-dark-dpr-{ratio}.png"
        assert shot in text, f"Missing screenshot path: {shot}"
        assert (ROOT / shot).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), shot
    for command in ("make check", "make test-browser", "make shots"):
        log = command.replace(" ", "-") + ".log"
        assert command in text and log in text, f"Missing gate evidence: {command}"
        output = (PACKET / log).read_text()
        assert output.startswith(f"Command: {command}\nExit code: 0\n"), log
        if command == "make shots":
            assert "shots: captured watch-dark.png" in output, log
            assert "shots: captured review-dark.png" in output, log
        else:
            assert re.search(r"\b\d+ passed\b", output), log
    print("PASS: hand-back, commits, visual comparison, two PNGs and three gate outputs")


if __name__ == "__main__":
    main()
