"""Bold detection: the stroke-width algorithm on controlled crops, and the
warning-region locator on a compliant fixture label."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

import fixture_corpus
from label_assay.extract.ocr import read_lines
from label_assay.match.bold import BoldVerdict, bold_ratio_verdict, check_warning_bold

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)


def _crop(text: str, *, bold: bool, size: int = 40) -> np.ndarray:
    names = ("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default(size)
    for name in names:
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    img = Image.new("L", (640, 72), color=255)
    ImageDraw.Draw(img).text((6, 6), text, font=font, fill=0)
    return np.asarray(img)


def test_bold_heading_beats_regular_body() -> None:
    head = _crop("GOVERNMENT WARNING", bold=True)
    body = _crop("according to the surgeon general", bold=False)
    assert bold_ratio_verdict(head, body).verdict == BoldVerdict.BOLD_OK


def test_regular_heading_is_not_flagged_bold() -> None:
    head = _crop("GOVERNMENT WARNING", bold=False)
    body = _crop("according to the surgeon general", bold=False)
    assert bold_ratio_verdict(head, body).verdict != BoldVerdict.BOLD_OK


def test_tiny_text_abstains_to_review() -> None:
    # Below the ~14px cap-height floor the check must not commit either way.
    head = _crop("GOVERNMENT WARNING", bold=True, size=12)
    body = _crop("according to the surgeon general", bold=False, size=12)
    assert bold_ratio_verdict(head, body).verdict == BoldVerdict.REVIEW


def test_compliant_fixture_bold_heading_is_not_falsely_failed() -> None:
    from label_assay.extract.ocr import read_lines

    image = FIXTURE.read_bytes()
    result = check_warning_bold(image, read_lines(image))
    # The fixture's heading is genuinely bold; the check must never FAIL it.
    assert result.verdict != BoldVerdict.NOT_BOLD


def _font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


@pytest.mark.parametrize("name", ["cola_24100001000210.png", "cola_25150001000637.png"])
def test_real_registry_labels_are_not_falsely_called_not_bold(name: str) -> None:
    # Two TTB-approved composites with visibly bolder headings used to come back
    # not_bold: one prints heading and body at different sizes (the same-size
    # premise broken), the other measures a stroke ratio of ~0.98 — noise, not
    # evidence, at 2-4px stroke widths. Both must abstain, never fail.
    cola = fixture_corpus.TESTS / "fixtures" / "cola" / name
    image = cola.read_bytes()
    result = check_warning_bold(image, read_lines(image))
    assert result.verdict != BoldVerdict.NOT_BOLD


def test_heading_on_its_own_line_abstains_instead_of_failing() -> None:
    # Narrow-label layout: OCR returns the bold heading as its own line, so there
    # is no same-line body to compare against. Splitting anyway measures the
    # heading against a sliver of itself and flunks a genuinely bold heading;
    # the honest verdict is a human's call, never NOT_BOLD.
    from label_assay.extract.ocr import read_lines

    head_font = _font(("arialbd.ttf", "DejaVuSans-Bold.ttf"), 36)
    body_font = _font(("arial.ttf", "DejaVuSans.ttf"), 24)
    img = Image.new("RGB", (720, 240), "white")
    draw = ImageDraw.Draw(img)
    draw.text((24, 24), "GOVERNMENT WARNING:", font=head_font, fill="black")
    body = (
        "According to the Surgeon General, women should",
        "not drink alcoholic beverages during pregnancy",
        "because of the risk of birth defects.",
    )
    for i, line in enumerate(body):
        draw.text((24, 84 + i * 36), line, font=body_font, fill="black")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    image = buffer.getvalue()

    result = check_warning_bold(image, read_lines(image))
    assert result.verdict != BoldVerdict.NOT_BOLD
