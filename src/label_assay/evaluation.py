"""Offline rule-engine regression gate. Not a vision or regulatory-approval score."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from label_assay.domain.models import Application, Verdict
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.rulebook.loader import load_rulebook
from label_assay.verify.engine import verify


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Input(_Strict):
    brand: str | None
    application_brand: str
    alcohol: str | None
    warning: Literal["reference", "lowercase_heading", "altered", "missing"]
    ocr: Literal["none", "match", "contradict_warning", "contradict_brand"]


class _Evidence(_Strict):
    detail: str = Field(min_length=1)
    diff: list[tuple[str, str, str]]


class _Case(_Strict):
    id: str = Field(min_length=1, max_length=100)
    input: _Input
    expected: dict[str, Verdict]
    expected_evidence: dict[str, _Evidence] = Field(min_length=1)
    verdict: Verdict


class _Manifest(_Strict):
    schema_version: Literal[1]
    rulebook_version: str
    scope: str
    cases: list[_Case] = Field(min_length=1, max_length=1000)


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _inputs(case: _Input, reference: str):
    heading, separator, body = reference.partition(":")
    altered = reference.rsplit(" ", 1)[0] + " changed."
    warning = {
        "reference": reference,
        "lowercase_heading": heading.title() + separator + body,
        "altered": altered,
        "missing": None,
    }[case.warning]

    def field(text):
        return ExtractedField(verbatim=text, value=text, found=text is not None)

    extraction = Extraction(
        brand_name=field(case.brand), class_type=field("Vodka"),
        alcohol_content=field(case.alcohol), net_contents=field("750 mL"),
        government_warning=field(warning),
    )
    lines = None
    if case.ocr != "none":
        texts = [case.brand, "Vodka", case.alcohol, "750 mL", warning]
        if case.ocr == "contradict_warning":
            texts[-1] = altered
        if case.ocr == "contradict_brand":
            texts[0] = "ZXQ Holdings"
        lines = [OcrLine(text=text, confidence=0.99) for text in texts if text]
    return extraction, Application(brand_name=case.application_brand, class_type="Vodka"), lines


def evaluate(raw: dict, *, verifier=verify) -> dict:
    """Reject stale/incomplete baselines; account for every expected and actual rule."""
    manifest = _Manifest.model_validate(raw)
    rulebook = load_rulebook()
    if manifest.rulebook_version != rulebook.version:
        raise ValueError("Rulebook changed. Review the expected outcomes before updating the baseline.")
    ids = [case.id for case in manifest.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation case IDs must be unique.")
    applicable = rulebook.rules_for("spirits")
    rules = {rule.id for rule in applicable}
    if len(rules) != len(applicable) or not rules or any(
        set(case.expected) != rules or not set(case.expected_evidence) <= rules
        for case in manifest.cases
    ):
        raise ValueError("Every case must declare exactly every applicable rule's expected verdict.")
    by_rule = {rule.id: rule for rule in applicable}
    reference = next(rule.match.reference for rule in rulebook.rules
                     if rule.match.strategy == "verbatim" and rule.match.reference)
    counts = Counter({verdict.value: 0 for verdict in Verdict})
    confusion = {rule: {expected.value: {actual.value: 0 for actual in Verdict}
                        for expected in Verdict} for rule in sorted(rules)}
    errors, outcomes = [], []
    for case in manifest.cases:
        extraction, application, lines = _inputs(case.input, reference)
        report = verifier(extraction, application, rulebook, ocr_lines=lines)
        actual_ids = [finding.rule_id for finding in report.findings]
        problems = []
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != rules:
            problems.append("Missing, additional or duplicate rule findings")
        if report.rulebook_version != rulebook.version:
            problems.append("Report rulebook version changed")
        if report.verdict != case.verdict:
            problems.append(f"Overall: expected {case.verdict}, got {report.verdict}")
        for finding in report.findings:
            actual = Verdict(finding.verdict).value
            counts[actual] += 1
            if finding.rule_id in case.expected:
                rule = by_rule[finding.rule_id]
                if (finding.title, finding.citation) != (rule.title, rule.citation):
                    problems.append(f"{finding.rule_id}: title or legal citation changed")
                expected = case.expected[finding.rule_id].value
                confusion[finding.rule_id][expected][actual] += 1
                if actual != expected:
                    problems.append(f"{finding.rule_id}: expected {expected}, got {actual}")
                if finding.rule_id in case.expected_evidence:
                    evidence = case.expected_evidence[finding.rule_id]
                    if (finding.detail, finding.diff) != (evidence.detail, evidence.diff):
                        problems.append(f"{finding.rule_id}: detail or text differences changed")
        errors.extend(f"{case.id}: {problem}" for problem in problems)
        outcomes.append({"id": case.id, "passed": not problems,
                         "report": report.model_dump(mode="json")})
    baseline = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "schema_version": 1, "passed": not errors, "scope": manifest.scope,
        "rulebook_version": rulebook.version,
        "baseline_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
        "cases": len(outcomes), "decisions": sum(counts.values()),
        "actual_counts": dict(counts), "confusion_by_rule": confusion,
        "errors": errors, "outcomes": outcomes,
    }
