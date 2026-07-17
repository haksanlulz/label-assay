"""Bold detection: the stroke-width algorithm on controlled crops, and the
warning-region locator on a compliant fixture label."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

import fixture_corpus
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


@pytest.mark.skipif(not FIXTURE.exists(), reason="run tools/make_test_labels.py first")
def test_compliant_fixture_bold_heading_is_not_falsely_failed() -> None:
    from label_assay.extract.ocr import read_lines

    image = FIXTURE.read_bytes()
    result = check_warning_bold(image, read_lines(image))
    # The fixture's heading is genuinely bold; the check must never FAIL it.
    assert result.verdict != BoldVerdict.NOT_BOLD
