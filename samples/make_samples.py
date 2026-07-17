"""Generate synthetic label images plus a matching application CSV.

The assignment suggests AI-generated test labels; these are programmatic so they
are reproducible and carry a known ground truth. One label is compliant and three
are each wrong in a different, specific way, so a batch run demonstrates the real
check set rather than a wall of passes.

The warning heading is rendered bold (the rest regular) so the bold check has a
real case, and larger than a true 1-2mm warning so it is judgeable from pixels.

Run:  uv run python samples/make_samples.py
Fonts: Arial on Windows, DejaVu elsewhere.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent

# The mandated statement (27 CFR 16.21), for the compliant sample.
WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)
# Same words, heading in title case — a capitalization violation under 16.22(a)(2).
WARNING_TITLE_CASE = WARNING.replace("GOVERNMENT WARNING:", "Government Warning:", 1)

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


def _draw_warning(draw: ImageDraw.ImageDraw, x: int, y: int, right: int, text: str, *, bold_heading: bool) -> None:
    bold, regular = _font("bold", 22), _font("regular", 22)
    space = draw.textlength(" ", font=regular)
    cx, cy, line_h = x, y, 30
    for i, word in enumerate(text.split(" ")):
        heading = i < 2  # the first two words are the mandated heading
        font = bold if (heading and bold_heading) else regular
        w = draw.textlength(word, font=font)
        if cx + w > right:
            cx, cy = x, cy + line_h
        draw.text((cx, cy), word, font=font, fill="black")
        cx += w + space


def _label(
    filename: str,
    *,
    brand: str,
    class_type: str,
    alcohol: str,
    net_contents: str = "750 mL",
    warning: str = WARNING,
    bold_heading: bool = True,
) -> Path:
    img = Image.new("RGB", (700, 900), "white")
    d = ImageDraw.Draw(img)
    d.text((350, 90), brand, font=_font("bold", 34), fill="black", anchor="mm")
    d.text((350, 190), class_type, font=_font("regular", 24), fill="black", anchor="mm")
    d.text((350, 270), alcohol, font=_font("regular", 22), fill="black", anchor="mm")
    d.text((350, 330), net_contents, font=_font("regular", 22), fill="black", anchor="mm")
    _draw_warning(d, 45, 520, 655, warning, bold_heading=bold_heading)
    path = OUT / filename
    img.save(path)
    return path


# filename -> (label spec, the application filed for it). The CSV pairs them, the
# way a real batch arrives: labels plus the data filed on their applications.
SAMPLES = [
    (
        "bourbon_compliant.png",
        dict(brand="OLD TOM DISTILLERY", class_type="Kentucky Straight Bourbon Whiskey",
             alcohol="45% Alc./Vol. (90 Proof)"),
        ("Old Tom Distillery", "Kentucky Straight Bourbon Whiskey"),
    ),
    (
        # Heading in title case and not bold: violates 27 CFR 16.22(a)(2).
        "bourbon_bad_warning.png",
        dict(brand="OLD TOM DISTILLERY", class_type="Kentucky Straight Bourbon Whiskey",
             alcohol="45% Alc./Vol. (90 Proof)", warning=WARNING_TITLE_CASE, bold_heading=False),
        ("Old Tom Distillery", "Kentucky Straight Bourbon Whiskey"),
    ),
    (
        # Proof does not equal twice the ABV (27 CFR 5.1).
        "bourbon_bad_proof.png",
        dict(brand="OLD TOM DISTILLERY", class_type="Kentucky Straight Bourbon Whiskey",
             alcohol="45% Alc./Vol. (100 Proof)"),
        ("Old Tom Distillery", "Kentucky Straight Bourbon Whiskey"),
    ),
    (
        # The label's brand is not the brand filed on the application.
        "bourbon_wrong_brand.png",
        dict(brand="RIVER BEND DISTILLERY", class_type="Kentucky Straight Bourbon Whiskey",
             alcohol="45% Alc./Vol. (90 Proof)"),
        ("Old Tom Distillery", "Kentucky Straight Bourbon Whiskey"),
    ),
]


def main() -> None:
    rows = []
    for filename, spec, (app_brand, app_class) in SAMPLES:
        path = _label(filename, **spec)
        rows.append({"filename": filename, "brand_name": app_brand, "class_type": app_class})
        print(f"wrote {path}")

    csv_path = OUT / "applications.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["filename", "brand_name", "class_type"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
