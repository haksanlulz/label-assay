"""The application service: bounded decode, read concurrency, and the distinct
clean failure messages the web shell shows for each reader.
"""

from __future__ import annotations

import time

import pytest

import fixture_corpus
from label_assay.domain.models import Application
from label_assay.extract.base import Extraction
from label_assay.web import service
from label_assay.web.service import ExtractionUnavailable, check_label
from synthetic_images import bomb_png

SPEC = fixture_corpus.known_good_compliant()
# fixture_path raises at import when the committed PNG is missing, so the whole
# module fails collection loudly rather than skipping.
FIXTURE = fixture_corpus.fixture_path(SPEC)


class _StubExtractor:
    """Returns the perfect read for the fixture; optionally slow or failing."""

    def __init__(self, delay: float = 0.0, error: Exception | None = None) -> None:
        self.delay = delay
        self.error = error
        self.window: tuple[float, float] | None = None
        self.calls = 0

    def extract(self, image: bytes) -> Extraction:
        self.calls += 1
        start = time.perf_counter()
        if self.delay:
            time.sleep(self.delay)
        self.window = (start, time.perf_counter())
        if self.error is not None:
            raise self.error
        return fixture_corpus.perfect_extraction(SPEC)


def test_ocr_and_vision_reads_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    # The two reads share no inputs; running them back to back doubles latency.
    # Sequential execution makes the recorded windows disjoint, which this fails.
    ocr_window = {}

    def slow_read_lines(image: bytes, *, background: bool = False) -> list:
        start = time.perf_counter()
        time.sleep(0.25)
        ocr_window["span"] = (start, time.perf_counter())
        return []

    monkeypatch.setattr(service, "read_lines", slow_read_lines)
    extractor = _StubExtractor(delay=0.25)
    check_label(FIXTURE.read_bytes(), fixture_corpus.application_for(SPEC), extractor=extractor)

    ocr_start, ocr_end = ocr_window["span"]
    vision_start, vision_end = extractor.window
    assert ocr_start < vision_end and vision_start < ocr_end, "the two reads ran sequentially"


def test_ocr_failure_reports_the_scanner_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_read_lines(image: bytes, *, background: bool = False) -> list:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(service, "read_lines", broken_read_lines)
    with pytest.raises(ExtractionUnavailable, match="label scanner"):
        check_label(FIXTURE.read_bytes(), Application(), extractor=_StubExtractor())


def test_vision_failure_reports_the_reader_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "read_lines", lambda image, background=False: [])
    with pytest.raises(ExtractionUnavailable, match="AI label reader"):
        check_label(
            FIXTURE.read_bytes(), Application(), extractor=_StubExtractor(error=RuntimeError("api down"))
        )


def test_decompression_bomb_is_rejected_before_decode_or_spend() -> None:
    # 48 MP decodes to ~144 MB; several at once OOM-kill a 2 GB machine. The
    # bytes sail through the upload guards, so the pixel bound must catch it —
    # before the raster is allocated and before the paid call is made.
    bomb = bomb_png(8000, 6000)
    assert len(bomb) < 5 * 1024 * 1024  # passes the per-file byte cap
    assert bomb.startswith(b"\x89PNG\r\n\x1a\n")  # and the magic check

    extractor = _StubExtractor()
    with pytest.raises(ExtractionUnavailable, match="too large"):
        check_label(bomb, Application(), extractor=extractor)
    assert extractor.calls == 0


def test_oversized_pixels_are_rejected_by_the_ocr_reader() -> None:
    # Defense in depth: the decode site itself enforces the same bound.
    from label_assay.extract.images import ImageTooLarge
    from label_assay.extract.ocr import read_lines

    with pytest.raises(ImageTooLarge):
        read_lines(bomb_png(8000, 6000))


def test_undecodable_bytes_get_a_clean_message() -> None:
    junk = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # magic passes, header does not parse
    with pytest.raises(ExtractionUnavailable, match="could not be read as an image"):
        check_label(junk, Application(), extractor=_StubExtractor())
