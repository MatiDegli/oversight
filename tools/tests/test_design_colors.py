"""Check color data and WCAG arithmetic without pinning design prose."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = (ROOT / "DESIGN.md").read_text()


def target_overrides() -> dict[str, str]:
    return dict(re.findall(r"^\| `([\w-]+)` \| `([^`]+)` \|$", DESIGN, re.MULTILINE))


def luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return sum(
        value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )


def test_v4_surface_overrides_are_explicit_color_data() -> None:
    overrides = target_overrides()
    assert set(overrides) == {
        "shell",
        "panel",
        "field",
        "inset",
        "page",
        "card",
        "card-bottom",
        "raised",
        "divider",
        "border",
        "top-highlight",
        "card-shadow",
    }
    assert overrides["field"] == overrides["inset"]


def test_contrast_matrices_use_v4_target_surfaces() -> None:
    front = DESIGN.split("---")[1].split("typography:")[0]
    colors = dict(re.findall(r'^  ([\w-]+): "([^"\n]+)"$', front, re.MULTILINE))
    colors.update(target_overrides())
    surfaces = ["shell", "panel", "page", "card", "card-bottom", "raised", "inset"]
    rows = re.findall(
        r"^\| ([\w-]+) \| ((?:\*?\*?[\d.]+\*?\*? \| ){6}"
        r"\*?\*?[\d.]+\*?\*?) \|$",
        DESIGN,
        re.MULTILINE,
    )
    assert len(rows) == 28
    for index, (token, cells) in enumerate(rows):
        floor = 4.5 if index < 18 else 3
        foreground = luminance(colors[token])
        for surface, cell in zip(surfaces, cells.split(" | "), strict=True):
            background = luminance(colors[surface])
            ratio = (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05)
            assert f"{ratio:.2f}" == cell.strip("*"), (token, surface)
            assert cell.startswith("**") == (ratio < floor), (token, surface)
