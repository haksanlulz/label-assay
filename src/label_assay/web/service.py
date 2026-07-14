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


class ExtractionUnavailable(Exception):
    """The label could not be read (no key configured, or the reader failed).
    The message is safe to show a user."""


def default_extractor(settings: Settings) -> ExtractorPort:
    if not settings.anthropic_api_key:
        raise ExtractionUnavailable("The label reader is not configured on this server.")
    return HaikuExtractor(api_key=settings.anthropic_api_key, model=settings.haiku_model)


def check_label(image: bytes, application: Application, *, extractor: ExtractorPort) -> LabelReport:
    ocr_lines = read_lines(image)
    try:
        extraction = extractor.extract(image)
    except Exception as exc:  # network / API / decode — surface cleanly, never a 500
        raise ExtractionUnavailable("The label reader was unavailable. Please try again.") from exc
    return verify(extraction, application, load_rulebook(), image=image, ocr_lines=ocr_lines)
