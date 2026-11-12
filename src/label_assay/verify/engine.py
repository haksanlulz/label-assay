"""The compliance engine — pure.

``verify(...)`` dispatches each applicable rule to the matcher registered for its
strategy, collects a Finding per rule (each carrying the rule's CFR citation),
and returns a LabelReport. It never branches on an individual rule and never
calls a model.

Two safety layers wrap the matchers:

- The legibility gate (when OCR is supplied): a finding drawn from a field the
  independent OCR read cannot corroborate is held for review, never passed or
  failed.
- Worst-finding aggregation: any FAIL fails; else any NEEDS_REVIEW needs review;
  else PASS only when at least one check actually passed — a report with no
  findings, or nothing but abstentions, is held for review. NOT_EVALUABLE
  findings never force a verdict.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from label_assay.domain.models import Application, Finding, LabelReport, Verdict
from label_assay.extract.base import Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.match.brand import BrandVerdict, match_brand
from label_assay.match.warning import WarningVerdict, compare_warning
from label_assay.rulebook.loader import Rule, Rulebook
from label_assay.text.numbers import read_alcohol_content
from label_assay.text.normalize import canon_brand
from label_assay.verify.confidence import (
    corroborates_exactly, corroborates_heading_capitals, ocr_is_alive, scan_differences,
    unconfirmed_fields,
)


@dataclass(frozen=True)
class VerifyContext:
    extraction: Extraction
    application: Application
    ocr_lines: list[OcrLine] | None = None
    image: bytes | None = None


def infer_beverage_class(class_type: str | None) -> str:
    t = (class_type or "").lower()
    if any(w in t for w in ("wine", "port", "sherry", "vermouth", "madeira", "champagne")):
        return "wine"
    if any(w in t for w in ("beer", "ale", "lager", "porter", "stout", "malt")):
        return "malt"
    return "spirits"


def _finding(rule: Rule, verdict: Verdict, detail: str, diff: tuple = ()) -> Finding:
    return Finding(
        rule_id=rule.id,
        title=rule.title,
        citation=rule.citation,
        verdict=verdict,
        detail=detail,
        diff=list(diff),
    )


_WARNING_VERDICTS = {
    WarningVerdict.MATCH: Verdict.PASS,
    WarningVerdict.CAPITALIZATION: Verdict.FAIL,
    WarningVerdict.ALTERED: Verdict.FAIL,
    # "removed" vs. "we couldn't read it" is indistinguishable, so absence
    # routes to review rather than a silent auto-fail.
    WarningVerdict.ABSENT: Verdict.NEEDS_REVIEW,
}


_SCAN_NOTE_LIMIT = 4


def _scan_note(differences: list[tuple[str, str]]) -> str:
    """Tell the reviewer where the scan and the reader part ways. The scan's
    read is a pointer, not a finding: it is quoted, and never decides."""
    if not differences:
        return ""
    parts = [f"read “{expected}” as “{found}”" if found else f"did not read “{expected}”"
             for expected, found in differences[:_SCAN_NOTE_LIMIT]]
    more = len(differences) - _SCAN_NOTE_LIMIT
    if more > 0:
        parts.append(f"differed in {more} more place{'s' if more > 1 else ''}")
    return (" The scan " + "; ".join(parts) + ". A scan can misread small print, "
            "so check those words on the label.")


def _match_warning_verbatim(rule: Rule, ctx: VerifyContext) -> Finding:
    field = getattr(ctx.extraction, rule.match.field)
    reference = rule.match.reference or ""
    result = compare_warning(field.verbatim, reference)
    verdict = _WARNING_VERDICTS[result.verdict]
    # Recitation defense (ADR-0002): a vision model can quote the mandated text
    # over a label that prints something subtly different, and the generic fuzzy
    # gate scores that near-perfect. A PASS on this field therefore also requires
    # the independent OCR read to contain the reference itself; anything less is
    # a human's call.
    if verdict is Verdict.PASS and ctx.ocr_lines is not None:
        if not corroborates_exactly(reference, ctx.ocr_lines):
            return _finding(
                rule, Verdict.NEEDS_REVIEW,
                "The reader's transcription matches the mandated warning, but an "
                "independent scan of the image did not confirm the statement "
                "word-for-word, so a person should verify it."
                + _scan_note(scan_differences(reference, ctx.ocr_lines)),
            )
        if not corroborates_heading_capitals(reference, ctx.ocr_lines):
            return _finding(
                rule, Verdict.NEEDS_REVIEW,
                "The independent scan did not confirm the warning heading's required "
                "capital letters, so a person should verify them.",
            )
    return _finding(rule, verdict, result.detail, result.diff)


_BRAND_VERDICTS = {
    BrandVerdict.MATCH: Verdict.PASS,
    BrandVerdict.REVIEW: Verdict.NEEDS_REVIEW,
    BrandVerdict.MISMATCH: Verdict.FAIL,
}


def _match_brand(rule: Rule, ctx: VerifyContext) -> Finding:
    application = ctx.application
    if not application.brand_name:
        return _finding(rule, Verdict.NOT_EVALUABLE, "No application brand name was provided to compare against.")
    field = getattr(ctx.extraction, rule.match.field)
    label_value = field.value
    if field.verbatim and field.value and canon_brand(field.verbatim) != canon_brand(field.value):
        return _finding(
            rule, Verdict.NEEDS_REVIEW,
            "The reader's quoted brand and cleaned brand differ; a person should "
            "check the label against the filed brand name.",
        )
    brand = match_brand(label_value, application.brand_name)
    if not application.fanciful_name:
        return _finding(rule, _BRAND_VERDICTS[brand.verdict], brand.detail)

    # A filing carries a brand name and, optionally, a fanciful name (both are
    # items on Form 5100.31), and real labels often display the fanciful name
    # most prominently. A match to the fanciful name alone is useful context,
    # but cannot establish that the required brand also appears on the label.
    fanciful = match_brand(label_value, application.fanciful_name)
    if brand.verdict is BrandVerdict.MATCH:
        return _finding(
            rule, Verdict.PASS,
            f"The label matches the filed brand name ({application.brand_name!r}) after normalization."
        )
    if fanciful.verdict is BrandVerdict.MATCH:
        return _finding(
            rule, Verdict.NEEDS_REVIEW,
            "The label read matches the filed fanciful name, but the required brand name "
            "was not confirmed. Check the label for the filed brand name.",
        )
    if brand.verdict is BrandVerdict.REVIEW:
        return _finding(rule, Verdict.NEEDS_REVIEW, brand.detail)
    if fanciful.verdict is BrandVerdict.REVIEW:
        return _finding(rule, Verdict.NEEDS_REVIEW, "Compared against the filed fanciful name: " + fanciful.detail)
    return _finding(
        rule,
        Verdict.FAIL,
        f"The brand read from the label ({label_value!r}) matches neither the filed "
        f"brand name ({application.brand_name!r}) nor the filed fanciful name "
        f"({application.fanciful_name!r}).",
    )


def _match_abv_consistency(rule: Rule, ctx: VerifyContext) -> Finding:
    field = getattr(ctx.extraction, rule.match.field)
    reading = read_alcohol_content(field.verbatim or field.value)
    content = reading.content
    if content is None:
        # "Nothing stated" and "two things stated" are different review jobs;
        # the detail says which, or "80 and 100 proof" reads as unreadable.
        detail = ("Could not read the alcohol content to check it." if reading.reason is None
                  else f"Could not check the alcohol content: {reading.reason}.")
        return _finding(rule, Verdict.NEEDS_REVIEW, detail)
    if content.proof_matches_abv is False:
        return _finding(
            rule,
            Verdict.FAIL,
            f"The stated proof ({content.proof}) does not equal twice the alcohol "
            f"by volume ({content.abv}).",
        )
    return _finding(rule, Verdict.PASS, "The stated alcohol content is internally consistent.")


def _match_warning_bold(rule: Rule, ctx: VerifyContext) -> Finding:
    if ctx.image is None or not ctx.ocr_lines:
        return _finding(rule, Verdict.NOT_EVALUABLE, "Boldness was not checked (image or OCR not available).")
    try:
        # Imported lazily so the engine stays importable without the CV dependencies.
        from label_assay.match.bold import BoldVerdict, check_warning_bold

        result = check_warning_bold(ctx.image, ctx.ocr_lines)
    except Exception:
        # A vision-library failure degrades this one check; it never sinks a
        # verdict. Logged because a silent degrade would also hide a permanent
        # regression — a dead regulatory check looks identical to a hard image.
        logging.getLogger(__name__).exception(
            "warning_bold check failed; degrading to NOT_EVALUABLE"
        )
        return _finding(rule, Verdict.NOT_EVALUABLE, "Boldness could not be checked on this image.")

    mapping = {
        BoldVerdict.BOLD_OK: Verdict.PASS,
        BoldVerdict.NOT_BOLD: Verdict.FAIL,
        BoldVerdict.REVIEW: Verdict.NEEDS_REVIEW,
    }
    return _finding(rule, mapping[result.verdict], result.detail)


# Strategy name -> matcher. The rulebook selects the strategy; the engine never
# names an individual rule. The loader's KNOWN_STRATEGIES is pinned equal to
# this registry by a test, so a rule that loads but silently never runs cannot
# exist; the skip below is defensive only.
_MATCHERS: dict[str, Callable[[Rule, VerifyContext], Finding]] = {
    "verbatim": _match_warning_verbatim,
    "brand_match": _match_brand,
    "abv_consistency": _match_abv_consistency,
    "warning_bold": _match_warning_bold,
}

_REVIEW_NOTE = " (Unconfirmed: a second reading of the label did not corroborate this, so a person should verify it.)"
_DEAD_OCR_NOTE = " (Unconfirmed: the independent OCR scan could not read the label, so nothing corroborates this and a person should verify it.)"


def verify(
    extraction: Extraction,
    application: Application,
    rulebook: Rulebook,
    *,
    ocr_lines: list[OcrLine] | None = None,
    image: bytes | None = None,
) -> LabelReport:
    ctx = VerifyContext(extraction, application, ocr_lines, image)
    # Prefer the filed class; fall back to what the label itself says (batch mode).
    bev = infer_beverage_class(application.class_type or (extraction.class_type.value or ""))
    unconfirmed = unconfirmed_fields(extraction, ocr_lines) if ocr_lines is not None else set()
    # A dead OCR read corroborates nothing; "a second reading did not corroborate
    # this" would claim a reading that never happened.
    review_note = _DEAD_OCR_NOTE if ocr_lines is not None and not ocr_is_alive(ocr_lines) else _REVIEW_NOTE

    findings: list[Finding] = []
    for rule in rulebook.rules_for(bev):
        matcher = _MATCHERS.get(rule.match.strategy)
        if matcher is None:
            continue
        finding = matcher(rule, ctx)
        if rule.match.field in unconfirmed and finding.verdict in (Verdict.PASS, Verdict.FAIL):
            finding = _finding(rule, Verdict.NEEDS_REVIEW, finding.detail + review_note)
        findings.append(finding)

    verdicts = {f.verdict for f in findings}
    if Verdict.FAIL in verdicts:
        overall = Verdict.FAIL
    elif Verdict.NEEDS_REVIEW in verdicts:
        overall = Verdict.NEEDS_REVIEW
    elif Verdict.PASS not in verdicts:
        # No applicable checks ran, or every one abstained (all NOT_EVALUABLE).
        # Either way there is zero positive evidence — that is a review, never
        # a silent pass. Unreachable with the shipped rulebook (the warning
        # matcher always decides or reviews), but the invariant must hold by
        # construction, not by rulebook shape.
        overall = Verdict.NEEDS_REVIEW
    else:
        overall = Verdict.PASS

    return LabelReport(verdict=overall, findings=findings, rulebook_version=rulebook.version)
