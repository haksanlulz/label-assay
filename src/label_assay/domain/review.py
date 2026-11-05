"""Portable human decisions over an unchanged, normalized automated report.

The digest detects accidental report substitution. Neither it nor the
self-entered reviewer name authenticates a person or signs a decision.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import TypeAdapter

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from label_assay.domain.models import Finding, LabelReport

CHOICES = {"pending": "Pending review", "agree": "Agree with finding",
           "request_correction": "Request correction", "escalate": "Escalate to a specialist"}

MAX_REVIEW_SOURCE_BYTES = 5 * 1024 * 1024
# The source can expand threefold when non-ASCII UTF-8 is JSON-escaped.
# 500 notes of 2,000 characters can add 12 MB as escaped surrogate pairs,
# plus repeated rule IDs, overall notes and metadata. Keep both encodings
# portable without truncating reviewer text; bound the actual encoded file.
MAX_REVIEW_RECORD_BYTES = 32 * 1024 * 1024


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Finding(Finding):
    model_config = ConfigDict(extra="forbid")
    rule_id: str = Field(min_length=1, max_length=200)


class _Report(LabelReport):
    model_config = ConfigDict(extra="forbid")
    findings: list[_Finding] = Field(min_length=1, max_length=500)


class ReviewSourceV1(_Strict):
    filename: str = Field(min_length=1, max_length=1000)
    checked_at: str | None = Field(max_length=100)
    report: _Report

    @model_validator(mode="after")
    def unique_findings(self):
        ids = [finding.rule_id for finding in self.report.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("Report findings must have unique rule IDs.")
        return self


class ReviewSource(ReviewSourceV1):
    app_version: str | None = Field(default=None, max_length=100)


class Decision(_Strict):
    rule_id: str
    choice: Literal["pending", "agree", "request_correction", "escalate"]
    note: str = Field(max_length=2000)


def report_digest(source: ReviewSourceV1) -> str:
    canonical = json.dumps(source.model_dump(mode="json"), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReviewRecordV1(_Strict):
    schema_version: Literal[1] = 1
    kind: Literal["label-assay-human-review"] = "label-assay-human-review"
    source_report: ReviewSourceV1
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=120)
    reviewed_at: AwareDatetime
    decisions: list[Decision] = Field(min_length=1, max_length=500)
    notes: str = Field(max_length=4000)
    complete: bool

    @model_validator(mode="after")
    def consistent_evidence(self):
        if not self.reviewer.strip():
            raise ValueError("Enter a reviewer name or reference.")
        if self.report_sha256 != report_digest(self.source_report):
            raise ValueError("The report digest does not match the included report.")
        rules = {finding.rule_id for finding in self.source_report.report.findings}
        ids = [decision.rule_id for decision in self.decisions]
        if len(ids) != len(set(ids)) or set(ids) != rules:
            raise ValueError("Include exactly one decision for every report finding.")
        if self.complete != all(decision.choice != "pending" for decision in self.decisions):
            raise ValueError("Completion must reflect whether any findings remain pending.")
        return self


class ReviewRecord(ReviewRecordV1):
    schema_version: Literal[2] = 2
    source_report: ReviewSource


_REVIEW_ADAPTER = TypeAdapter(Annotated[ReviewRecordV1 | ReviewRecord, Field(discriminator="schema_version")])


def make_review(source: dict, reviewer: str, decisions: list[dict], notes: str) -> ReviewRecord:
    normalized = ReviewSource.model_validate(source)
    return ReviewRecord(
        source_report=normalized, report_sha256=report_digest(normalized),
        reviewer=reviewer.strip(), reviewed_at=datetime.now(timezone.utc),
        decisions=decisions, notes=notes,
        complete=all(decision.get("choice") != "pending" for decision in decisions),
    )


def validate_review(value: dict) -> ReviewRecordV1 | ReviewRecord:
    return _REVIEW_ADAPTER.validate_python(value)


def _check_record_size(content: bytes) -> None:
    if len(content) > MAX_REVIEW_RECORD_BYTES:
        raise ValueError("Review record exceeds 32 MiB.")


def dump_review(record: ReviewRecordV1 | ReviewRecord) -> bytes:
    content = json.dumps(record.model_dump(mode="json"), ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode("utf-8")
    _check_record_size(content)
    return content


def load_review(content: bytes) -> ReviewRecordV1 | ReviewRecord:
    _check_record_size(content)
    return validate_review(json.loads(content))
