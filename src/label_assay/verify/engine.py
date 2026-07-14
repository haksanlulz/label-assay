"""The compliance engine — pure.

``verify(extraction, application, rulebook)`` dispatches each applicable rule to
the matcher registered for its strategy, collects a Finding per rule (each
carrying the rule's CFR citation), and returns a LabelReport. It never branches
on an individual rule and never calls a model.

Overall verdict = the worst finding: any FAIL fails; else any NEEDS_REVIEW needs
review; else PASS. NOT_EVALUABLE findings (a rule that cannot be checked from the
artifact) never force a verdict.
"""

from __future__ import annotations

from collections.abc import Callable

from label_assay.domain.models import Application, Finding, LabelReport, Verdict
from label_assay.extract.base import Extraction
from label_assay.match.brand import BrandVerdict, match_brand
from label_assay.match.warning import WarningVerdict, compare_warning
from label_assay.rulebook.loader import Rule, Rulebook
from label_assay.text.numbers import parse_alcohol_content


def infer_beverage_class(class_type: str | None) -> str:
    t = (class_type or "").lower()
    if any(w in t for w in ("wine", "port", "sherry", "vermouth", "madeira", "champagne")):
        return "wine"
    if any(w in t for w in ("beer", "ale", "lager", "porter", "stout", "malt")):
        return "malt"
    return "spirits"


def _finding(rule: Rule, verdict: Verdict, detail: str) -> Finding:
    return Finding(rule_id=rule.id, citation=rule.citation, verdict=verdict, detail=detail)


def _match_warning_verbatim(rule: Rule, extraction: Extraction, application: Application) -> Finding:
    field = getattr(extraction, rule.match.field)
    result = compare_warning(field.verbatim, rule.match.reference or "")
    mapping = {
        WarningVerdict.MATCH: Verdict.PASS,
        WarningVerdict.CAPITALIZATION: Verdict.FAIL,
        WarningVerdict.ALTERED: Verdict.FAIL,
        # "removed" and "we couldn't read it" are indistinguishable until the
        # confidence gate (OCR cross-check) lands, so absence routes to review,
        # never a silent auto-fail.
        WarningVerdict.ABSENT: Verdict.NEEDS_REVIEW,
    }
    return _finding(rule, mapping[result.verdict], result.detail)


def _match_brand(rule: Rule, extraction: Extraction, application: Application) -> Finding:
    label_value = getattr(extraction, rule.match.field).value
    result = match_brand(label_value, application.brand_name)
    mapping = {
        BrandVerdict.MATCH: Verdict.PASS,
        BrandVerdict.REVIEW: Verdict.NEEDS_REVIEW,
        BrandVerdict.MISMATCH: Verdict.FAIL,
    }
    return _finding(rule, mapping[result.verdict], result.detail)


def _match_abv_consistency(rule: Rule, extraction: Extraction, application: Application) -> Finding:
    field = getattr(extraction, rule.match.field)
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


# Strategy name -> matcher. The rulebook selects the strategy; the engine never
# names an individual rule. A rule whose strategy has no matcher yet is skipped.
_MATCHERS: dict[str, Callable[[Rule, Extraction, Application], Finding]] = {
    "verbatim": _match_warning_verbatim,
    "brand_match": _match_brand,
    "abv_consistency": _match_abv_consistency,
}


def verify(
    extraction: Extraction,
    application: Application,
    rulebook: Rulebook,
    *,
    beverage_class: str | None = None,
) -> LabelReport:
    bev = beverage_class or infer_beverage_class(application.class_type)
    findings: list[Finding] = []
    for rule in rulebook.rules_for(bev):
        matcher = _MATCHERS.get(rule.match.strategy)
        if matcher is not None:
            findings.append(matcher(rule, extraction, application))

    verdicts = {f.verdict for f in findings}
    if Verdict.FAIL in verdicts:
        overall = Verdict.FAIL
    elif Verdict.NEEDS_REVIEW in verdicts:
        overall = Verdict.NEEDS_REVIEW
    else:
        overall = Verdict.PASS

    return LabelReport(verdict=overall, findings=findings, rulebook_version=rulebook.version)
