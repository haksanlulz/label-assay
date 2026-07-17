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
from label_assay.extract.images import ImageTooLarge, downscale_for_vision, preview_jpeg
from label_assay.web.service import check_label
from synthetic_images import bomb_png


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


def test_preview_is_jpeg_downscaled_and_metadata_free() -> None:
    # The page preview: bounded size, and the re-encode must not carry the
    # upload's EXIF (a phone scan's GPS tags have no business in the page).
    exif = Image.Exif()
    exif[271] = "Make with no business on a web page"  # tag 271 = Make
    buffer = io.BytesIO()
    Image.new("RGB", (2400, 1000), "white").save(buffer, format="JPEG", exif=exif)
    out = preview_jpeg(buffer.getvalue())
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert max(img.size) <= 1200
    assert not img.getexif()


def test_preview_handles_modes_jpeg_cannot_carry() -> None:
    # PNG uploads are commonly RGBA or palette; the preview must re-encode them
    # rather than let PIL refuse the JPEG save.
    for mode in ("RGBA", "P", "1"):
        buffer = io.BytesIO()
        Image.new(mode, (100, 80)).save(buffer, format="PNG")
        img = Image.open(io.BytesIO(preview_jpeg(buffer.getvalue())))
        assert img.format == "JPEG"


def test_preview_goes_through_the_bounded_decode_guard() -> None:
    # The upload was already decoded once for OCR under open_bounded; the
    # preview encoder must sit behind the same guard, not decode unchecked.
    with pytest.raises(ImageTooLarge):
        preview_jpeg(bomb_png(9000, 9000))  # 81 MP, over the 40 MP bound


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
