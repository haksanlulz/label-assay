"""downscale_for_vision: the vision call gets bounded bytes (the hosted API
rejects >8000 px images outright and downscales the rest server-side), while OCR
and the typography crops keep the original full-resolution bytes."""

from __future__ import annotations

import io

import pytest
from PIL import Image

import label_assay.web.service as service
from label_assay.domain.models import Application
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.images import downscale_for_vision
from label_assay.web.service import check_label


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_small_images_pass_through_byte_identical() -> None:
    data = _png(640, 480)
    assert downscale_for_vision(data) is data


def test_tall_composites_are_capped_under_the_api_limit() -> None:
    data = _png(300, 8192)  # taller than the vision API's 8000 px maximum
    out = downscale_for_vision(data)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 1568
    assert out.startswith(b"\x89PNG")  # stays PNG, so the adapter's sniffing holds


class _SpyExtractor:
    def __init__(self) -> None:
        self.received: bytes | None = None

    def extract(self, image: bytes) -> Extraction:
        self.received = image
        f = ExtractedField(verbatim=None, found=False, value=None)
        return Extraction(
            brand_name=f, class_type=f, alcohol_content=f, net_contents=f, government_warning=f
        )


def test_check_label_downscales_only_the_vision_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_by_ocr: list[bytes] = []

    def spy_read_lines(image: bytes, *, background: bool = False):
        seen_by_ocr.append(image)
        return []

    monkeypatch.setattr(service, "read_lines", spy_read_lines)
    data = _png(300, 8192)
    spy = _SpyExtractor()
    check_label(data, Application(), extractor=spy)
    assert seen_by_ocr == [data]  # OCR read the original, full-resolution bytes
    assert spy.received is not None and spy.received != data
    assert max(Image.open(io.BytesIO(spy.received)).size) <= 1568
