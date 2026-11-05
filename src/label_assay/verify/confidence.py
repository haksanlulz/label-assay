"""Legibility gate — cross-check the vision read against an independent OCR read.

The two channels fail in different ways: OCR errors are local and random, while a
vision model's errors are coherent and prior-driven (it can reproduce a value it
did not actually read). So per field we ask one cheap, high-signal question — does
the model's quoted text actually appear in the OCR of the same image? — and when
it does not, we refuse to PASS or FAIL that field: it goes to a human. A supplied
OCR read that cannot read anything also leaves asserted fields unconfirmed.
Omitting OCR altogether is a separate engine-only mode used by offline callers.
"""

from __future__ import annotations

import re
from decimal import Decimal

from rapidfuzz import fuzz

from label_assay.extract.base import Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.text.normalize import squash as _squash

_SUPPORT_FLOOR = 0.60  # below this, the model's quote is not corroborated by OCR
_FIELDS = tuple(Extraction.model_fields)
_NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])\s*(%|percent\b|proof\b)?", re.I)


def ocr_is_alive(lines: list[OcrLine]) -> bool:
    """Did OCR read anything usable at all?"""
    return sum(len(line.text) for line in lines) >= 8 and any(line.confidence >= 0.5 for line in lines)


def field_support(verbatim: str, ocr_blob: str) -> float:
    """0..1 — how well the model's quoted text is found within the OCR text."""
    quoted, blob = _squash(verbatim), ocr_blob
    if not quoted or not blob:
        return 0.0
    if len(quoted) > len(blob):
        # partial_ratio slides the shorter string over the longer, so with a
        # quote longer than the whole OCR read it would measure the OCR being
        # contained in the quote — containment backwards, and a perfect score
        # for a model reciting a full text over a label that prints a fragment
        # of it. Containment is impossible here; score the full comparison.
        return fuzz.ratio(quoted, blob) / 100.0
    return fuzz.partial_ratio(quoted, blob) / 100.0


def corroborates_exactly(reference: str, ocr_lines: list[OcrLine]) -> bool:
    """Does the OCR read contain ``reference`` verbatim, on the squashed
    alphanumerics? Space-, punctuation-, and case-insensitive, but character-
    exact otherwise. The fuzzy floor above is the wrong tool for the mandated
    warning: a one-word alteration on the label still scores ~0.98 against a
    recited quote, so a PASS on that field requires the independent read to
    contain the statute itself."""
    target = _squash(reference)
    return bool(target) and target in _squash(" ".join(line.text for line in ocr_lines))


def corroborates_heading_capitals(reference: str, ocr_lines: list[OcrLine]) -> bool:
    """Confirm capitals on the heading attached to the corroborated statement."""
    heading = "".join(ch for ch in reference.partition(":")[0] if ch.isalnum())
    expected = "".join(ch for ch in reference if ch.isalnum()).casefold()
    observed = "".join(ch for line in ocr_lines for ch in line.text if ch.isalnum())
    return bool(heading) and any(
        observed[index:index + len(heading)] == heading and
        observed[index:index + len(expected)].casefold() == expected
        for index in range(max(0, len(observed) - len(expected) + 1))
    )


def _numeric_evidence(asserted: str, ocr_text: str) -> bool:
    """Require the ordered numeric values and any stated units to agree.

    Fuzzy text similarity can consider 40%/90 proof and 45%/90 proof nearly
    identical. Treat percent and proof as distinct units, and preserve order so
    the endpoints of a range cannot be silently exchanged.
    """
    def tokens(text: str) -> list[tuple[Decimal, str]]:
        result = []
        for match in _NUMBER.finditer(text):
            unit = (match.group(2) or "").casefold()
            result.append((Decimal(match.group(1)), "%" if unit == "percent" else unit))
        return result

    expected, actual = tokens(asserted), tokens(ocr_text)
    if not expected:
        return True
    for start in range(len(actual) - len(expected) + 1):
        window = actual[start:start + len(expected)]
        if all(number == seen and (not unit or unit == seen_unit)
               for (number, unit), (seen, seen_unit) in zip(expected, window)):
            return True
    return False


def unconfirmed_fields(extraction: Extraction, ocr_lines: list[OcrLine]) -> set[str]:
    """Fields whose vision assertions the supplied OCR does not corroborate.
    Findings from these fields must be held for review, never passed or failed."""
    if not ocr_is_alive(ocr_lines):
        return {name for name in _FIELDS if (getattr(extraction, name).verbatim
                                             or getattr(extraction, name).value)}
    ocr_text = " ".join(line.text for line in ocr_lines)
    blob = _squash(ocr_text)
    unconfirmed: set[str] = set()
    for name in _FIELDS:
        field = getattr(extraction, name)
        # Matchers may use either the quote or the cleaned value. Corroborating
        # only the quote lets a contradictory value pass the brand matcher.
        asserted = (field.verbatim, field.value)
        if any(value and (field_support(value, blob) < _SUPPORT_FLOOR or
                          (name == "alcohol_content" and not _numeric_evidence(value, ocr_text)))
               for value in asserted):
            unconfirmed.add(name)
    return unconfirmed
