"""Colour contrast, recomputed from the stylesheet on every run.

docs/ACCESSIBILITY.md claims WCAG 2.1 AA; badge text is normal-size, so every ink/
background pair it uses must clear 4.5:1. Computing the ratios from app.css
itself means a token edit that drops a pair under the bar fails the suite
instead of silently falsifying the claim (the stock USWDS gold did exactly
that at 4.07:1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CSS = Path(__file__).resolve().parents[1] / "src" / "label_assay" / "web" / "static" / "app.css"

# fg token, bg token — the pairs the badge and alert styles actually compose.
_PAIRS = [
    ("ok-ink", "ok-bg"),
    ("warn-ink", "warn-bg"),
    ("err-ink", "err-bg"),
    ("info-ink", "info-bg"),
    ("muted", "bg-subtle"),  # the not_evaluable badge
    ("ink", "bg"),
]


def _tokens() -> dict[str, str]:
    text = _CSS.read_text(encoding="utf-8")
    root = re.search(r":root\s*\{(.*?)\}", text, re.S)
    assert root, "app.css :root token block not found"
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root.group(1)))


def _luminance(hex_color: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _ratio(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize(("fg", "bg"), _PAIRS)
def test_text_pairs_meet_aa_contrast(fg: str, bg: str) -> None:
    tokens = _tokens()
    ratio = _ratio(tokens[fg], tokens[bg])
    assert ratio >= 4.5, f"--{fg} on --{bg} is {ratio:.2f}:1, under the 4.5:1 AA bar"
