"""Legibility gate — cross-check the vision read against an independent OCR read.

The two channels fail in different ways: OCR errors are local and random, while a
vision model's errors are coherent and prior-driven (it can reproduce a value it
did not actually read). So per field we ask one cheap, high-signal question — does
the model's quoted text actually appear in the OCR of the same image? — and when
it does not, and OCR is otherwise alive, we refuse to PASS or FAIL that field: it
goes to a human.

Absence of OCR evidence is never treated as evidence of absence. If OCR could not
read the label at all, no field is singled out; the caller decides what a
globally illegible image means.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from label_assay.extract.base import Extraction
from label_assay.extract.ocr import OcrLine

_ALNUM = re.compile(r"[^a-z0-9]")
_SUPPORT_FLOOR = 0.60  # below this, the model's quote is not corroborated by OCR
_FIELDS = ("brand_name", "class_type", "alcohol_content", "net_contents", "government_warning")


def _squash(s: str) -> str:
    """Space- and punctuation-insensitive form. OCR often drops spaces between
    rendered words ("OLDTOMDISTILLERY"), so corroboration is checked on the
    collapsed alphanumerics rather than token by token."""
    return _ALNUM.sub("", s.casefold())


def ocr_is_alive(lines: list[OcrLine]) -> bool:
    """Did OCR read anything usable at all?"""
    return sum(len(line.text) for line in lines) >= 8 and any(line.confidence >= 0.5 for line in lines)


def field_support(verbatim: str, ocr_blob: str) -> float:
    """0..1 — how well the model's quoted text is found within the OCR text."""
    quoted, blob = _squash(verbatim), ocr_blob
    if not quoted or not blob:
        return 0.0
    return fuzz.partial_ratio(quoted, blob) / 100.0


def unconfirmed_fields(extraction: Extraction, ocr_lines: list[OcrLine]) -> set[str]:
    """Fields whose vision-quoted text the OCR does not corroborate (OCR alive).
    Findings from these fields must be held for review, never passed or failed."""
    if not ocr_is_alive(ocr_lines):
        return set()  # can't corroborate anything; not a per-field failure
    blob = _squash(" ".join(line.text for line in ocr_lines))
    unconfirmed: set[str] = set()
    for name in _FIELDS:
        field = getattr(extraction, name)
        if not field.verbatim:
            continue  # nothing quoted to corroborate; absence is the engine's call
        if field_support(field.verbatim, blob) < _SUPPORT_FLOOR:
            unconfirmed.add(name)
    return unconfirmed
