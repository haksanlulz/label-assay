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

from rapidfuzz import fuzz

from label_assay.extract.base import Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.text.normalize import squash as _squash

_SUPPORT_FLOOR = 0.60  # below this, the model's quote is not corroborated by OCR
_FIELDS = ("brand_name", "class_type", "alcohol_content", "net_contents", "government_warning")


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


def unconfirmed_fields(extraction: Extraction, ocr_lines: list[OcrLine]) -> set[str]:
    """Fields whose vision-quoted text the OCR does not corroborate (OCR alive).
    Findings from these fields must be held for review, never passed or failed."""
    if not ocr_is_alive(ocr_lines):
        return set()  # can't corroborate anything; not a per-field failure
    blob = _squash(" ".join(line.text for line in ocr_lines))
    unconfirmed: set[str] = set()
    for name in _FIELDS:
        field = getattr(extraction, name)
        # Corroborate what the matchers actually consume: the quote when the
        # model gave one, else the value. A value asserted without a quote must
        # not bypass the gate — that shape is the hallucination this module
        # exists to catch, not an exemption from it.
        asserted = field.verbatim or field.value
        if not asserted:
            continue  # nothing asserted to corroborate; absence is the engine's call
        if field_support(asserted, blob) < _SUPPORT_FLOOR:
            unconfirmed.add(name)
    return unconfirmed
