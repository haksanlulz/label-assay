"""Application service — ties extraction and verification together for the web
shell, and turns infrastructure failures into a clean, user-facing signal
instead of a stack trace.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

from label_assay.config import Settings
from label_assay.domain.models import Application, LabelReport
from label_assay.extract.base import Extraction, ExtractorPort
from label_assay.extract.haiku import HaikuExtractor
from label_assay.extract.images import ImageTooLarge, downscale_for_vision, open_bounded
from label_assay.extract.ocr import read_lines
from label_assay.rulebook.loader import load_rulebook
from label_assay.verify.engine import verify
from label_assay.web.budget import BudgetExhausted, DailyBudget

logger = logging.getLogger(__name__)


class ExtractionUnavailable(Exception):
    """The label could not be read (no key configured, the reader failed, or the
    day's spend limit is reached). The message is safe to show a user."""


class CheckResult(NamedTuple):
    """One check's verdict report together with the extraction it was judged
    from. The result page shows the reviewer what the reader actually returned,
    and carrying the extraction here keeps extractor types out of the domain
    report."""

    report: LabelReport
    extraction: Extraction


# One extractor per (key, model): the SDK client owns an httpx connection pool,
# and building a fresh one per request pays a new TCP+TLS handshake against the
# 5-second target. The batch path already reuses one; this makes the single path
# match.
_EXTRACTORS: dict[tuple[str, str], ExtractorPort] = {}


def default_extractor(settings: Settings) -> ExtractorPort:
    if not settings.anthropic_api_key:
        raise ExtractionUnavailable("The label reader is not configured on this server.")
    key = (settings.anthropic_api_key, settings.haiku_model)
    extractor = _EXTRACTORS.get(key)
    if extractor is None:
        extractor = _EXTRACTORS[key] = HaikuExtractor(api_key=key[0], model=key[1])
    return extractor


def check_label(
    image: bytes,
    application: Application,
    *,
    extractor: ExtractorPort,
    budget: DailyBudget | None = None,
    background: bool = False,
) -> CheckResult:
    # ``background=True`` marks a batch item: its OCR read yields to any pending
    # interactive check at the engine's priority gate (see extract/ocr.py).
    # Reject undecodable or bomb-sized images before any money or CPU is spent.
    # The header alone carries the dimensions, so this costs microseconds. The
    # vision call then gets a bounded copy: the hosted API rejects images over
    # 8000 px on a side and downscales the rest server-side anyway, so sending a
    # registry-size composite full-size buys nothing and loses the tallest ones
    # entirely. OCR and the typography crops keep the original bytes — the
    # corroboration gate depends on reading the fine print.
    try:
        open_bounded(image)
        vision_bytes = downscale_for_vision(image)
    except ImageTooLarge as exc:
        raise ExtractionUnavailable(
            "That image is too large to process. Please upload a smaller scan of the label."
        ) from exc
    except Exception as exc:
        logger.exception("Image decode failed before extraction")
        raise ExtractionUnavailable("That file could not be read as an image.") from exc

    # Account for the paid call before making it, so a public instance cannot be
    # driven past its daily bound.
    if budget is not None:
        try:
            budget.reserve()
        except BudgetExhausted as exc:
            raise ExtractionUnavailable(str(exc)) from exc

    # The two reads share only the image bytes, so they run concurrently and a
    # check costs max(vision call, OCR) rather than the sum — the 5-second target
    # does not survive running them back to back. OCR stays on this thread (it is
    # already serialized under its own lock); the network-bound vision call gets
    # the worker. The readers fail for different reasons and are worth telling
    # apart, both for the person looking at the page and for whoever has to fix
    # the server.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        vision = pool.submit(extractor.extract, vision_bytes)
        try:
            ocr_lines = read_lines(image, background=background)
        except Exception as exc:  # OCR engine failure is infrastructure, not a verdict
            logger.exception("OCR read failed")
            raise ExtractionUnavailable(
                "The label scanner could not run on this server. Please try again later."
            ) from exc
        try:
            extraction = vision.result()
        except Exception as exc:  # network / API / decode — surface cleanly, never a 500
            logger.exception("Vision extraction failed")
            raise ExtractionUnavailable(
                "The AI label reader was unavailable. Please try again."
            ) from exc
    finally:
        # wait=False so an OCR failure reports immediately instead of holding the
        # page for the vision call's timeout; the stray call finishes on its own.
        pool.shutdown(wait=False)

    report = verify(extraction, application, load_rulebook(), image=image, ocr_lines=ocr_lines)
    return CheckResult(report=report, extraction=extraction)
