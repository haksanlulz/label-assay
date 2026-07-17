"""Application service — ties extraction and verification together for the web
shell, and turns infrastructure failures into a clean, user-facing signal
instead of a stack trace.
"""

from __future__ import annotations

from label_assay.config import Settings
from label_assay.domain.models import Application, LabelReport
from label_assay.extract.base import ExtractorPort
from label_assay.extract.haiku import HaikuExtractor
from label_assay.extract.ocr import read_lines
from label_assay.rulebook.loader import load_rulebook
from label_assay.verify.engine import verify
from label_assay.web.budget import BudgetExhausted, DailyBudget


class ExtractionUnavailable(Exception):
    """The label could not be read (no key configured, the reader failed, or the
    day's spend limit is reached). The message is safe to show a user."""


def default_extractor(settings: Settings) -> ExtractorPort:
    if not settings.anthropic_api_key:
        raise ExtractionUnavailable("The label reader is not configured on this server.")
    return HaikuExtractor(api_key=settings.anthropic_api_key, model=settings.haiku_model)


def check_label(
    image: bytes,
    application: Application,
    *,
    extractor: ExtractorPort,
    budget: DailyBudget | None = None,
) -> LabelReport:
    # Account for the paid call before making it, so a public instance cannot be
    # driven past its daily bound.
    if budget is not None:
        try:
            budget.reserve()
        except BudgetExhausted as exc:
            raise ExtractionUnavailable(str(exc)) from exc

    # The two readers fail for different reasons and are worth telling apart, both
    # for the person looking at the page and for whoever has to fix the server.
    try:
        ocr_lines = read_lines(image)
    except Exception as exc:  # OCR engine/decode failure is infrastructure, not a verdict
        raise ExtractionUnavailable(
            "The label scanner could not run on this server. Please try again later."
        ) from exc

    try:
        extraction = extractor.extract(image)
    except Exception as exc:  # network / API / decode — surface cleanly, never a 500
        raise ExtractionUnavailable(
            "The AI label reader was unavailable. Please try again."
        ) from exc

    return verify(extraction, application, load_rulebook(), image=image, ocr_lines=ocr_lines)
