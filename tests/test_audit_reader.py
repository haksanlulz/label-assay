"""Regressions from the 2026-11-12 audit of the readers and the spend bound.
Each test failed on the code as it stood at e684c21."""

from __future__ import annotations

import io
import math

import numpy as np
import pytest
from PIL import Image

from label_assay.config import Settings
from label_assay.extract.images import downscale_for_vision, open_bounded, preview_jpeg
from label_assay.web.budget import BudgetExhausted, DailyBudget


def _png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _sixteen_bit_label() -> bytes:
    """Light paper (30000 of 65535) with a dark block of 'text' (2000)."""
    pixels = np.full((60, 60), 30000, dtype=np.uint16)
    pixels[20:40, 10:50] = 2000
    return _png(Image.fromarray(pixels))


def _transparent_label() -> bytes:
    """Black 'text' on a fully transparent background."""
    img = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    img.paste((0, 0, 0, 255), (10, 20, 50, 40))
    return _png(img)


def _luma(img: Image.Image, xy: tuple[int, int]) -> int:
    return img.convert("L").getpixel(xy)


# --- 16-bit grayscale scans (READER-1) ---

def test_sixteen_bit_scan_keeps_its_contrast_everywhere_it_is_decoded() -> None:
    data = _sixteen_bit_label()
    for name, img in (
        ("open_bounded", open_bounded(data)),
        ("vision copy", Image.open(io.BytesIO(downscale_for_vision(data)))),
        ("preview", Image.open(io.BytesIO(preview_jpeg(data)))),
        ("ocr raster", open_bounded(data).convert("RGB")),
    ):
        paper, ink = _luma(img, (5, 5)), _luma(img, (30, 30))
        assert paper - ink > 60, f"{name}: paper {paper}, ink {ink}"


# --- transparent artwork (READER-2) ---

def test_transparent_background_becomes_white_not_black() -> None:
    data = _transparent_label()
    for name, img in (
        ("open_bounded", open_bounded(data)),
        ("vision copy", Image.open(io.BytesIO(downscale_for_vision(data)))),
        ("preview", Image.open(io.BytesIO(preview_jpeg(data)))),
        ("ocr raster", open_bounded(data).convert("RGB")),
    ):
        assert _luma(img, (5, 5)) > 240, f"{name}: background {_luma(img, (5, 5))}"
        assert _luma(img, (30, 30)) < 15, f"{name}: text {_luma(img, (30, 30))}"


def test_palette_transparency_is_flattened_too() -> None:
    img = Image.new("P", (20, 20), 0)
    img.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", transparency=0)
    assert _luma(open_bounded(buffer.getvalue()), (5, 5)) > 240


def test_ordinary_images_are_untouched() -> None:
    rgb = Image.new("RGB", (20, 20), (10, 120, 200))
    assert open_bounded(_png(rgb)).mode == "RGB"
    assert open_bounded(_png(rgb)).getpixel((3, 3)) == (10, 120, 200)
    gray = Image.new("L", (20, 20), 77)
    assert open_bounded(_png(gray)).getpixel((3, 3)) == 77


# --- the daily spend bound must fail closed (READER-3) ---

@pytest.mark.parametrize("limit", [math.nan, math.inf, -1.0])
def test_budget_refuses_a_limit_that_is_not_a_finite_non_negative_number(limit: float) -> None:
    with pytest.raises(ValueError, match="daily budget"):
        DailyBudget(limit_usd=limit)


@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_settings_refuse_a_broken_budget_value(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAILY_BUDGET_USD", value)
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_a_limit_corrupted_after_construction_still_refuses() -> None:
    budget = DailyBudget(limit_usd=1.0)
    budget.limit_usd = math.nan
    with pytest.raises(BudgetExhausted):
        budget.reserve()


def test_zero_budget_is_allowed_and_refuses_every_label() -> None:
    with pytest.raises(BudgetExhausted):
        DailyBudget(limit_usd=0.0).reserve()
