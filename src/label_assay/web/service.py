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
from label_assay.extract.images import (
    RIGHT_ANGLE_TRANSPOSES,
    ImageTooLarge,
    downscale_for_vision,
    open_bounded,
    transpose_image,
)
from label_assay.extract.ocr import OcrLine, read_lines
from label_assay.rulebook.loader import Rulebook, load_rulebook
from label_assay.verify.confidence import corroborates_exactly
from label_assay.verify.engine import verify
from label_assay.web.budget import BudgetExhausted, DailyBudget

logger = logging.getLogger(__name__)


class ExtractionUnavailable(Exception):
    """The label could not be read (no key configured, the reader failed, or the
    day's spend limit is reached). The message is safe to show a user."""


class CheckResult(NamedTuple):
    """One check's verdict report together with the extraction it was judged
    from and the image bytes the readers actually saw — rotated, when the
    operator said the upload was. The result page shows the reviewer what the
    reader returned and echoes exactly the raster that was judged; carrying
    the extraction here keeps extractor types out of the domain report."""

    report: LabelReport
    extraction: Extraction
    image: bytes


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


# Retry rotations for labels that print the mandated warning sideways along an
# edge (two of the eleven real registry composites in tests/fixtures/cola do).
_ROTATION_RETRIES = (90, 180, 270)


def _mandated_warning(rulebook: Rulebook) -> str | None:
    """The statutory warning text, read from the rulebook — its single owner."""
    for rule in rulebook.rules:
        if rule.match.strategy == "verbatim" and rule.match.field == "government_warning":
            return rule.match.reference
    return None


def _recover_rotated_warning(
    image: bytes, lines: list[OcrLine], *, background: bool
) -> list[OcrLine]:
    """Bounded rotation retry: when the upright OCR read does not contain the
    mandated warning text, re-read the image rotated 90, 180, and 270 degrees,
    stopping at the first rotation whose read does contain it, and append that
    pass's lines — marked with their rotation — so corroboration sees them.

    The vision model reads rotated text natively, but the OCR channel does not,
    so without this the corroboration gate holds every sideways-warning label
    for review. The cost bound: at most three extra OCR passes, paid only when
    the warning was not found upright — a label whose warning reads upright
    pays nothing. The vision call is never retried. Callers opt in per check
    (``check_label(recover_rotation=True)``): the batch path does by default,
    because nobody is waiting on any one of its labels; the interactive path
    never does, because its latency target does not admit serial retry passes
    — there the operator states the rotation up front instead.

    This lives in the service layer, not in the OCR spine, for two reasons.
    The loop must sit outside the engine slot: each pass here is its own
    read_lines call, so it takes the engine lock and the bounded decode exactly
    like the first pass and no lock is held between passes — a loop inside the
    slot would hold the lock for up to four inferences and break the priority
    gate's guarantee that an interactive check waits behind at most one. And
    deciding whether warning text was found takes the rulebook's reference,
    which the generic OCR reader deliberately does not know.
    """
    reference = _mandated_warning(load_rulebook())
    if reference is None or corroborates_exactly(reference, lines):
        return lines
    for rotation in _ROTATION_RETRIES:
        rotated = read_lines(image, background=background, rotation=rotation)
        if corroborates_exactly(reference, rotated):
            # Upright lines first: geometry consumers take the first heading
            # line they find, and a box in the upright frame is the measurable
            # one. The rotated lines carry their rotation as the marker.
            return [*lines, *rotated]
    return lines


def check_label(
    image: bytes,
    application: Application,
    *,
    extractor: ExtractorPort,
    budget: DailyBudget | None = None,
    background: bool = False,
    rotation: int = 0,
    recover_rotation: bool = False,
) -> CheckResult:
    # ``background=True`` marks a batch item: its OCR read yields to any pending
    # interactive check at the engine's priority gate (see extract/ocr.py).
    # ``rotation`` is the operator's statement of how the upload looks: the
    # clockwise right angle the label appears rotated by (0, 90, 180, or 270).
    # The raster is transposed once, losslessly, before anything reads it — an
    # image that looks rotated N degrees clockwise comes upright under the
    # N-degree counter-clockwise transpose, which is what the shared map
    # applies — so the vision copy, OCR, and the returned bytes all carry the
    # same corrected raster. ``recover_rotation`` opts in to the bounded
    # sideways-warning retry; the interactive path leaves it off because its
    # latency target is unconditional.
    if rotation and rotation not in RIGHT_ANGLE_TRANSPOSES:
        raise ValueError(f"rotation must be 0, 90, 180, or 270, not {rotation}")
    # Reject undecodable or bomb-sized images before any money or CPU is spent.
    # The header alone carries the dimensions, so this costs microseconds. The
    # vision call then gets a bounded copy: the hosted API rejects images over
    # 8000 px on a side and downscales the rest server-side anyway, so sending a
    # registry-size composite full-size buys nothing and loses the tallest ones
    # entirely. OCR and the typography crops keep the original bytes — the
    # corroboration gate depends on reading the fine print.
    try:
        if rotation:
            image = transpose_image(image, rotation)
        open_bounded(image)
        vision_bytes = downscale_for_vision(image)
    except ImageTooLarge as exc:
        raise ExtractionUnavailable(
            "That image is too large to process. Upload a smaller scan of the label."
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
            if recover_rotation:
                # At most three extra OCR passes, and only when the upright read
                # did not contain the mandated warning (see _recover_rotated_warning).
                ocr_lines = _recover_rotated_warning(image, ocr_lines, background=background)
        except Exception as exc:  # OCR engine failure is infrastructure, not a verdict
            logger.exception("OCR read failed")
            raise ExtractionUnavailable(
                "The label scanner could not run on this server. Try again later."
            ) from exc
        try:
            extraction = vision.result()
        except Exception as exc:  # network / API / decode — surface cleanly, never a 500
            logger.exception("Vision extraction failed")
            raise ExtractionUnavailable(
                "The AI label reader was unavailable. Try again."
            ) from exc
    finally:
        # wait=False so an OCR failure reports immediately instead of holding the
        # page for the vision call's timeout; the stray call finishes on its own.
        pool.shutdown(wait=False)

    report = verify(extraction, application, load_rulebook(), image=image, ocr_lines=ocr_lines)
    return CheckResult(report=report, extraction=extraction, image=image)
