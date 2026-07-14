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


class LabelReport(BaseModel):
    """The result for a whole product (which may span several label images)."""

    verdict: Verdict
    findings: list[Finding]
    rulebook_version: str
