"""downscale_for_vision: the vision call gets bounded bytes (the hosted API
rejects >8000 px images outright and downscales the rest server-side), while OCR
and the typography crops keep the original full-resolution bytes."""

from __future__ import annotations

import io

import pytest
from PIL import ExifTags, Image
from PIL.PngImagePlugin import PngInfo

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


def test_vision_copy_is_always_a_fresh_reencode_with_zero_exif() -> None:
    # A small upright JPEG used to pass through byte-identical — carrying its
    # EXIF (GPS position included) to the third-party API. The vision copy must
    # be a fresh re-encode on every path: no EXIF entries out, pixels unchanged.
    exif = Image.Exif()
    exif[271] = "PhoneMaker"  # tag 271 = Make
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (40.0, 44.0, 0.0)
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 30, 90)).save(buffer, format="JPEG", exif=exif)
    data = buffer.getvalue()
    src = Image.open(io.BytesIO(data))
    assert dict(src.getexif().get_ifd(ExifTags.IFD.GPSInfo))  # the threat is real in the input

    out = downscale_for_vision(data)
    assert out != data
    img = Image.open(io.BytesIO(out))
    assert not dict(img.getexif())  # zero EXIF entries on the egress copy
    assert not dict(img.getexif().get_ifd(ExifTags.IFD.GPSInfo))
    # Same visual content: the re-encode is lossless over the decoded raster.
    assert img.size == src.size
    assert img.convert("RGB").tobytes() == src.convert("RGB").tobytes()


def test_small_upright_png_never_carries_source_metadata() -> None:
    # The no-metadata guarantee holds for lossless sources too: a small PNG
    # with text and EXIF chunks comes out as a fresh pixel-only encode. (A
    # metadata-free PNG can re-encode to coincidentally identical bytes, so
    # the contract is pinned on the metadata, not on byte inequality.)
    exif = Image.Exif()
    exif[271] = "ScannerMaker"  # tag 271 = Make
    meta = PngInfo()
    meta.add_text("Comment", "operator note that must not egress")
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(buffer, format="PNG", pnginfo=meta, exif=exif)
    data = buffer.getvalue()
    assert b"operator note" in data  # the chunk really is in the source bytes

    out = downscale_for_vision(data)
    assert out is not data  # never the original byte string
    assert out.startswith(b"\x89PNG")
    assert b"operator note" not in out
    img = Image.open(io.BytesIO(out))
    assert not dict(img.getexif())
    assert img.size == (640, 480)  # already inside the bound: no downscale


def _png_chunk_names(data: bytes) -> set[str]:
    # A reviewer's-eye view of the egress bytes: the chunk names actually in
    # the stream, independent of what PIL chooses to surface in ``info``.
    names = set()
    offset = 8  # past the PNG signature
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        names.add(data[offset + 4 : offset + 8].decode("ascii"))
        offset += 12 + length  # 4-byte length + 4-byte name + payload + 4-byte CRC
    return names


def test_vision_copy_carries_no_icc_profile_from_jpeg_or_png_sources() -> None:
    # EXIF is not the only metadata that survives a decode: PIL's PNG save
    # copies the source's ICC profile out of ``im.info`` into a fresh iCCP
    # chunk unless told not to, and ICC profiles can carry vendor description
    # and copyright text. The pixels-only contract is chunk-level — nothing
    # beyond the structural chunks leaves for the third party.
    profile = b"fake profile carrying vendor text that must not egress"
    for fmt in ("JPEG", "PNG"):
        buffer = io.BytesIO()
        Image.new("RGB", (640, 480), "white").save(buffer, format=fmt, icc_profile=profile)
        data = buffer.getvalue()
        source_info = Image.open(io.BytesIO(data)).info
        assert source_info.get("icc_profile")  # the threat is real in the input

        out = downscale_for_vision(data)
        assert "icc_profile" not in Image.open(io.BytesIO(out)).info
        assert _png_chunk_names(out) <= {"IHDR", "IDAT", "IEND"}


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
    # A sideways-stored upload goes out upright: the re-encode bakes the EXIF
    # orientation into the pixels rather than depending on the provider
    # honoring the (now-stripped) tag.
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
