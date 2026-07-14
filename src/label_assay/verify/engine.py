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
  else PASS. NOT_EVALUABLE findings never force a verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from label_assay.domain.models import Application, Finding, LabelReport, Verdict
from label_assay.extract.base import Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.match.brand import BrandVerdict, match_brand
from label_assay.match.warning import WarningVerdict, compare_warning
from label_assay.rulebook.loader import Rule, Rulebook
from label_assay.text.numbers import parse_alcohol_content
from label_assay.verify.confidence import unconfirmed_fields


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
    return Finding(rule_id=rule.id, citation=rule.citation, verdict=verdict, detail=detail, diff=list(diff))


def _match_warning_verbatim(rule: Rule, ctx: VerifyContext) -> Finding:
    field = getattr(ctx.extraction, rule.match.field)
    result = compare_warning(field.verbatim, rule.match.reference or "")
    mapping = {
        WarningVerdict.MATCH: Verdict.PASS,
        WarningVerdict.CAPITALIZATION: Verdict.FAIL,
        WarningVerdict.ALTERED: Verdict.FAIL,
        # "removed" vs. "we couldn't read it" is indistinguishable, so absence
        # routes to review rather than a silent auto-fail.
        WarningVerdict.ABSENT: Verdict.NEEDS_REVIEW,
    }
    return _finding(rule, mapping[result.verdict], result.detail, result.diff)


def _match_brand(rule: Rule, ctx: VerifyContext) -> Finding:
    label_value = getattr(ctx.extraction, rule.match.field).value
    result = match_brand(label_value, ctx.application.brand_name)
    mapping = {
        BrandVerdict.MATCH: Verdict.PASS,
        BrandVerdict.REVIEW: Verdict.NEEDS_REVIEW,
        BrandVerdict.MISMATCH: Verdict.FAIL,
    }
    return _finding(rule, mapping[result.verdict], result.detail)


def _match_abv_consistency(rule: Rule, ctx: VerifyContext) -> Finding:
    field = getattr(ctx.extraction, rule.match.field)
    content = parse_alcohol_content(field.verbatim or field.value)
    if content is None:
        return _finding(rule, Verdict.NEEDS_REVIEW, "Could not read the alcohol content to check it.")
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
    # Imported lazily so the engine stays importable without the CV dependencies.
    from label_assay.match.bold import BoldVerdict, check_warning_bold

    result = check_warning_bold(ctx.image, ctx.ocr_lines)
    mapping = {
        BoldVerdict.BOLD_OK: Verdict.PASS,
        BoldVerdict.NOT_BOLD: Verdict.FAIL,
        BoldVerdict.REVIEW: Verdict.NEEDS_REVIEW,
    }
    return _finding(rule, mapping[result.verdict], result.detail)


# Strategy name -> matcher. The rulebook selects the strategy; the engine never
# names an individual rule. A rule whose strategy has no matcher yet is skipped.
_MATCHERS: dict[str, Callable[[Rule, VerifyContext], Finding]] = {
    "verbatim": _match_warning_verbatim,
    "brand_match": _match_brand,
    "abv_consistency": _match_abv_consistency,
    "warning_bold": _match_warning_bold,
}

_REVIEW_NOTE = " (Unconfirmed: a second reading of the label did not corroborate this, so a person should verify it.)"


def verify(
    extraction: Extraction,
    application: Application,
    rulebook: Rulebook,
    *,
    beverage_class: str | None = None,
    ocr_lines: list[OcrLine] | None = None,
    image: bytes | None = None,
) -> LabelReport:
    ctx = VerifyContext(extraction, application, ocr_lines, image)
    bev = beverage_class or infer_beverage_class(application.class_type)
    unconfirmed = unconfirmed_fields(extraction, ocr_lines) if ocr_lines is not None else set()

    findings: list[Finding] = []
    for rule in rulebook.rules_for(bev):
        matcher = _MATCHERS.get(rule.match.strategy)
        if matcher is None:
            continue
        finding = matcher(rule, ctx)
        if rule.match.field in unconfirmed and finding.verdict in (Verdict.PASS, Verdict.FAIL):
            finding = _finding(rule, Verdict.NEEDS_REVIEW, finding.detail + _REVIEW_NOTE)
        findings.append(finding)

    verdicts = {f.verdict for f in findings}
    if Verdict.FAIL in verdicts:
        overall = Verdict.FAIL
    elif Verdict.NEEDS_REVIEW in verdicts:
        overall = Verdict.NEEDS_REVIEW
    else:
        overall = Verdict.PASS

    return LabelReport(verdict=overall, findings=findings, rulebook_version=rulebook.version)
