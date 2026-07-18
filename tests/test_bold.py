"""Bold detection: the stroke-width algorithm on controlled crops, the
warning-region locator on a compliant fixture label, and the cross-line
clearance for headings that print as their own OCR line."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

import fixture_corpus
from label_assay.extract.ocr import OcrLine, read_lines
from label_assay.match.bold import BoldVerdict, bold_ratio_verdict, check_warning_bold

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)


def _crop(text: str, *, bold: bool, size: int = 40) -> np.ndarray:
    names = ("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")
    font = _font(names, size)
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
    image = FIXTURE.read_bytes()
    result = check_warning_bold(image, fixture_corpus.fixture_ocr_lines())
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


def test_heading_from_a_rotation_retry_pass_abstains() -> None:
    # A rotation-retry line's box is in the rotated frame; cropping the upright
    # image with it would measure whatever art happens to sit there. The
    # heading is located, honestly not measurable — never a verdict.
    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(buffer, format="PNG")
    line = OcrLine(
        text="GOVERNMENT WARNING: and enough body text on the same line to split",
        confidence=0.95,
        box=((10.0, 10.0), (390.0, 10.0), (390.0, 40.0), (10.0, 40.0)),
        rotation=90,
    )
    result = check_warning_bold(buffer.getvalue(), [line])
    assert result.verdict == BoldVerdict.REVIEW
    assert "rotat" in result.detail.lower()


_OWN_LINE_BODY = (
    "According to the Surgeon General, women should",
    "not drink alcoholic beverages during pregnancy",
    "because of the risk of birth defects.",
)


def _own_line_warning(*, head_bold: bool) -> bytes:
    """A narrow-label warning block: heading as its own line, statement body
    below it, all at one point size — the layout the cross-line clearance
    exists for. DejaVu is preferred over the Windows faces because it is the
    family CI resolves to; both families' clearances are measured (see the
    ratios quoted in the tests)."""
    head_names = ("DejaVuSans-Bold.ttf", "arialbd.ttf") if head_bold else ("DejaVuSans.ttf", "arial.ttf")
    head_font = _font(head_names, 26)
    body_font = _font(("DejaVuSans.ttf", "arial.ttf"), 26)
    img = Image.new("RGB", (820, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.text((24, 24), "GOVERNMENT WARNING:", font=head_font, fill="black")
    for i, line in enumerate(_OWN_LINE_BODY):
        draw.text((24, 63 + i * 39), line, font=body_font, fill="black")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def test_own_line_bold_heading_is_cleared_cross_line() -> None:
    # OCR returns the bold heading as its own line, so there is no same-line
    # body to compare against; the size-normalized cross-line ratio clears it.
    # Measured on this render through real OCR: 1.32 (DejaVu) / 1.24 (arial)
    # against the 1.15 floor.
    image = _own_line_warning(head_bold=True)
    result = check_warning_bold(image, read_lines(image))
    assert result.verdict == BoldVerdict.BOLD_OK
    assert "across lines" in result.detail


def test_own_line_regular_heading_reviews_never_passes_never_fails() -> None:
    # The false-pass guard on the clear-only path: the same render with a
    # regular-weight heading must not clear (measured 0.77 DejaVu / 0.82 arial
    # against the 1.15 floor) — and must not be convicted either, because the
    # cross-line normalization is an approximation. A person decides.
    image = _own_line_warning(head_bold=False)
    result = check_warning_bold(image, read_lines(image))
    assert result.verdict == BoldVerdict.REVIEW
    assert result.verdict != BoldVerdict.BOLD_OK
    assert result.verdict != BoldVerdict.NOT_BOLD
    assert "own line" in result.detail


def _own_line_scene(
    *, tiny_head: bool = False, n_body: int = 2, body_rotation: int = 0
) -> tuple[bytes, list[OcrLine]]:
    """A painted own-line scene with hand-built OCR lines, for exercising the
    cross-line guards one at a time. The heading is painted decisively heavy
    (bold face plus a 2px outline, the make_test_labels stabilization) so that
    the guard under test is the only thing standing between the scene and a
    clearance: the unmodified scene measures ~1.8, and a regressed guard flips
    the expected REVIEW to BOLD_OK instead of passing for the wrong reason."""
    head_size = 12 if tiny_head else 40
    body_size = 22
    img = Image.new("L", (760, 300), color=255)
    draw = ImageDraw.Draw(img)
    head_font = _font(("DejaVuSans-Bold.ttf", "arialbd.ttf"), head_size)
    body_font = _font(("DejaVuSans.ttf", "arial.ttf"), body_size)
    draw.text((24, 20), "GOVERNMENT WARNING:", font=head_font, fill=0, stroke_width=2, stroke_fill=0)
    head_box = ((20.0, 16.0), (700.0, 16.0), (700.0, 24.0 + head_size * 1.3), (20.0, 24.0 + head_size * 1.3))
    lines = [OcrLine(text="GOVERNMENT WARNING:", confidence=0.95, box=head_box, rotation=0)]
    y = 30 + head_size
    for text in _OWN_LINE_BODY[:n_body]:
        draw.text((24, y), text, font=body_font, fill=0)
        box = ((20.0, float(y - 4)), (700.0, float(y - 4)),
               (700.0, float(y + body_size * 1.4)), (20.0, float(y + body_size * 1.4)))
        lines.append(OcrLine(text=text, confidence=0.95, box=box, rotation=body_rotation))
        y += int(body_size * 1.6)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue(), lines


def test_own_line_scene_control_clears() -> None:
    # The anchor for the guard tests below: with two upright, measurable body
    # lines the decisively heavy heading clears cross-line.
    image, lines = _own_line_scene()
    result = check_warning_bold(image, lines)
    assert result.verdict == BoldVerdict.BOLD_OK


def test_cross_line_needs_at_least_two_measurable_body_lines() -> None:
    # One body line is one noisy sample, not a median; the check abstains with
    # the plain own-line message rather than clearing on it.
    image, lines = _own_line_scene(n_body=1)
    result = check_warning_bold(image, lines)
    assert result.verdict == BoldVerdict.REVIEW
    assert result.ratio is None


def test_cross_line_too_small_heading_abstains() -> None:
    # The heading is what the verdict is about; below the same ~14px cap-height
    # floor the same-line path uses, no cross-line clearance is attempted.
    image, lines = _own_line_scene(tiny_head=True)
    result = check_warning_bold(image, lines)
    assert result.verdict == BoldVerdict.REVIEW
    assert result.ratio is None


def test_cross_line_excludes_rotation_retry_body_lines() -> None:
    # Rotation-retry lines carry boxes in the rotated frame; cropping the
    # upright image with them would measure unrelated pixels, so they must not
    # count as body evidence — leaving too few lines here, hence abstention.
    image, lines = _own_line_scene(body_rotation=90)
    result = check_warning_bold(image, lines)
    assert result.verdict == BoldVerdict.REVIEW
    assert result.ratio is None
