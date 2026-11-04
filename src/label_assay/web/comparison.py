"""Compare saved evidence without re-running a model or inferring product identity."""
from __future__ import annotations

import json
from collections import Counter

from pydantic import ValidationError

from label_assay.domain.models import LabelReport, Verdict


def read_saved_report(content: bytes, index: int) -> dict:
    try:
        data = json.loads(content)
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Choose a LabelAssay full-report JSON export (schema version 1).")
        items = data.get("items")
        if not isinstance(items, list) or not 0 <= index < len(items):
            raise ValueError("Choose a valid label row from each report.")
        item = items[index]
        if not isinstance(item, dict) or item.get("status") != "done" or not item.get("report"):
            raise ValueError("That label has no completed findings to compare. Choose a completed row.")
        report = LabelReport.model_validate(item["report"])
        if len(report.findings) > 500 or len({f.rule_id for f in report.findings}) != len(report.findings):
            raise ValueError("Report findings must have unique rule IDs and contain at most 500 rules.")
        checked_at = item.get("checked_at")
        return {"filename": str(item.get("filename", "Unnamed label")), "checked_at": str(checked_at) if checked_at else None, "report": report}
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError) as exc:
        raise ValueError("This file is not a valid LabelAssay report. Export the full JSON report and try again.") from exc


def compare_reports(before: dict, after: dict) -> dict:
    old_report, new_report = before["report"], after["report"]
    old = {f.rule_id: f for f in old_report.findings}
    new = {f.rule_id: f for f in new_report.findings}
    same_rules = old_report.rulebook_version == new_report.rulebook_version
    rows = []
    for rule_id in dict.fromkeys([*old, *new]):
        a, b = old.get(rule_id), new.get(rule_id)
        if b is None:
            change = "No longer checked"
        elif a is None:
            change = "New check"
        elif b.verdict == Verdict.NOT_EVALUABLE:
            change = "Still uncheckable" if a.verdict == b.verdict else "Now uncheckable"
        elif b.verdict == Verdict.PASS and a.verdict != Verdict.PASS:
            change = "Resolved in this check" if same_rules and a.verdict in (Verdict.FAIL, Verdict.NEEDS_REVIEW) else "Now passes (coverage or rulebook changed)"
        elif b.verdict in (Verdict.FAIL, Verdict.NEEDS_REVIEW):
            change = "Still needs attention" if a.verdict in (Verdict.FAIL, Verdict.NEEDS_REVIEW) else "New attention needed"
        else:
            change = "Unchanged result"
        rows.append({"rule_id": rule_id, "title": (b or a).title, "change": change, "before": a, "after": b})
    return {"before": before, "after": after, "rows": rows, "same_rulebook": same_rules,
            "coverage_changed": set(old) != set(new) or {k for k, f in old.items() if f.verdict == Verdict.NOT_EVALUABLE} != {k for k, f in new.items() if f.verdict == Verdict.NOT_EVALUABLE},
            "counts": dict(Counter(r["change"] for r in rows))}
