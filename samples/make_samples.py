"""Generate synthetic label images for demos and tests.

The assignment suggests AI-generated test labels; these are programmatic so they
are reproducible and carry a known ground truth. "GOVERNMENT WARNING" is rendered
bold (the rest of the warning regular) so the Day-4 bold check has a real case.

Run:  uv run python samples/make_samples.py
Fonts: uses DejaVu (shipped with matplotlib/Pillow on Linux) or Arial on Windows.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent

# The mandated statement (27 CFR 16.21), reproduced for a *compliant* sample.
WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)

_FONT_CANDIDATES = {
    "regular": ("arial.ttf", "DejaVuSans.ttf"),
    "bold": ("arialbd.ttf", "DejaVuSans-Bold.ttf"),
}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for name in _FONT_CANDIDATES[weight]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _draw_warning(draw: ImageDraw.ImageDraw, x: int, y: int, right: int) -> None:
    # Rendered larger than a real 1-2mm warning so the bold check is judgeable
    # from pixels (below a ~14px cap height it correctly abstains to review).
    bold, regular = _font("bold", 22), _font("regular", 22)
    space = draw.textlength(" ", font=regular)
    cx, cy, line_h = x, y, 30
    for i, word in enumerate(WARNING.split(" ")):
        font = bold if i < 2 else regular  # "GOVERNMENT" and "WARNING:" are bold
        w = draw.textlength(word, font=font)
        if cx + w > right:
            cx, cy = x, cy + line_h
        draw.text((cx, cy), word, font=font, fill="black")
        cx += w + space


def make_bourbon_compliant() -> Path:
    img = Image.new("RGB", (700, 900), "white")
    d = ImageDraw.Draw(img)
    d.text((350, 90), "OLD TOM DISTILLERY", font=_font("bold", 34), fill="black", anchor="mm")
    d.text((350, 190), "Kentucky Straight Bourbon Whiskey", font=_font("regular", 24), fill="black", anchor="mm")
    d.text((350, 270), "45% Alc./Vol. (90 Proof)", font=_font("regular", 22), fill="black", anchor="mm")
    d.text((350, 330), "750 mL", font=_font("regular", 22), fill="black", anchor="mm")
    _draw_warning(d, 45, 520, 655)
    path = OUT / "bourbon_compliant.png"
    img.save(path)
    return path


if __name__ == "__main__":
    p = make_bourbon_compliant()
    print(f"wrote {p}")
