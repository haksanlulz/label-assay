"""Core domain types.

The compliance engine is a pure function of (extraction, application, rulebook).
It never raises: a compliance problem is a `Finding`, not an exception. Verdicts
are three-state so a label we could not read routes to a human instead of a
silent FAIL.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel


class Verdict(enum.StrEnum):
    PASS = "pass"
    NEEDS_REVIEW = "needs_review"  # could not verify automatically — a human decides
    FAIL = "fail"
    NOT_EVALUABLE = "not_evaluable"  # rule cannot be checked from a flat image (e.g. type size in mm)


class Severity(enum.StrEnum):
    FAIL = "fail"
    WARN = "warn"


class SurfaceType(enum.StrEnum):
    FRONT = "front"
    BACK = "back"
    NECK = "neck"
    OTHER = "other"


class Finding(BaseModel):
    """One rule's outcome against one product. Carries its CFR citation so the
    verdict is always traceable back to the regulation it rests on."""

    rule_id: str
    citation: str
    verdict: Verdict
    detail: str
    # Optional word-level differences (op, expected_span, found_span) for showing
    # a reviewer exactly what deviates — e.g. from the warning-text comparison.
    diff: list[tuple[str, str, str]] = []


class LabelReport(BaseModel):
    """The result for a whole product (which may span several label images)."""

    verdict: Verdict
    findings: list[Finding]
    rulebook_version: str


class Application(BaseModel):
    """The data filed on the COLA application — the "application data" side of
    the comparison.

    There is deliberately no alcohol-content or net-contents field: TTB Form
    5100.31 does not carry them, so alcohol content is checked for internal
    consistency, never against the application.
    """

    # All default to empty so a batch of loose label images (which carries no
    # per-label application) can be verified for label-internal compliance; the
    # brand match is then reported not-evaluable rather than forced.
    brand_name: str = ""
    class_type: str = ""
    # Form 5100.31 files an optional fanciful name alongside the brand name;
    # empty when none was filed. Labels often display it more prominently than
    # the brand, so the brand check accepts either filed name.
    fanciful_name: str = ""
