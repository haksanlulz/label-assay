"""downscale_for_vision: the vision call gets bounded bytes (the hosted API
rejects >8000 px images outright and downscales the rest server-side), while OCR
and the typography crops keep the original full-resolution bytes."""

from __future__ import annotations

import io

import pytest
from PIL import Image

import fixture_corpus
import label_assay.web.service as service
from label_assay.domain.models import Application
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.images import (
    ImageTooLarge,
    downscale_for_vision,
    open_bounded,
    preview_jpeg,
)
from label_assay.extract.ocr import OcrLine
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


def _exif_sideways_jpeg() -> bytes:
    # A phone-style upload: an 80x120 portrait (top half dark, bottom half
    # light) stored as its sideways raster with EXIF orientation 6, the tag a
    # camera writes to mean "rotate this back upright to display it".
    upright = Image.new("RGB", (80, 120), "white")
    upright.paste((0, 0, 0), (0, 0, 80, 60))
    stored = upright.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation
    buffer = io.BytesIO()
    stored.save(buffer, format="JPEG", quality=95, exif=exif)
    return buffer.getvalue()


def test_exif_orientation_is_applied_at_the_bounded_decode() -> None:
    # One place, every consumer: the decode path itself hands back upright
    # pixels, so OCR, the vision copy, and the preview never see the raster
    # sideways.
    img = open_bounded(_exif_sideways_jpeg())
    assert img.size == (80, 120)  # portrait again, not the stored landscape
    assert img.getpixel((40, 10))[0] < 64  # the dark half is back on top
    assert img.getpixel((40, 110))[0] > 192


def test_small_sideways_upload_is_reencoded_upright_for_vision() -> None:
    # The byte-identical shortcut must not ship sideways-stored pixels whose
    # uprightness depends on the provider honoring EXIF.
    data = _exif_sideways_jpeg()
    out = downscale_for_vision(data)
    assert out != data
    assert Image.open(io.BytesIO(out)).size == (80, 120)


def test_preview_of_a_sideways_upload_is_upright() -> None:
    img = Image.open(io.BytesIO(preview_jpeg(_exif_sideways_jpeg())))
    assert img.size == (80, 120)


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

    def spy_read_lines(image: bytes, *, background: bool = False, rotation: int = 0):
        seen_by_ocr.append(image)
        # A read containing the warning, so the rotation retry stays out of a
        # test that is about which bytes each reader receives.
        return [OcrLine(text=fixture_corpus.mandated_warning(), confidence=0.99)]

    monkeypatch.setattr(service, "read_lines", spy_read_lines)
    data = _png(300, 8192)
    spy = _SpyExtractor()
    check_label(data, Application(), extractor=spy)
    assert seen_by_ocr == [data]  # OCR read the original, full-resolution bytes
    assert spy.received is not None and spy.received != data
    assert max(Image.open(io.BytesIO(spy.received)).size) <= 1568
