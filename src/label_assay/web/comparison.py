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
        declared_version = data.get("app_version")
        app_version = (declared_version.strip() if isinstance(declared_version, str)
                       and 0 < len(declared_version.strip()) <= 100 else None)
        return {"filename": str(item.get("filename", "Unnamed label")),
                "checked_at": str(checked_at) if checked_at else None,
                "app_version": app_version, "report": report}
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError, RecursionError) as exc:
        # RecursionError: JSON nested thousands deep. Its own message describes
        # the interpreter's stack, which is nobody's business on a public page.
        raise ValueError("This file is not a valid LabelAssay report. Export the full JSON report and try again.") from exc


def compare_reports(before: dict, after: dict) -> dict:
    old_report, new_report = before["report"], after["report"]
    old = {f.rule_id: f for f in old_report.findings}
    new = {f.rule_id: f for f in new_report.findings}
    old_rules, new_rules = old_report.rulebook_version.strip(), new_report.rulebook_version.strip()
    same_rules = None if not old_rules or not new_rules else old_rules == new_rules
    old_version, new_version = before.get("app_version"), after.get("app_version")
    software_versions = ("unknown" if not old_version or not new_version else
                         "same_declared" if old_version == new_version else "different_declared")
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
            change = "Now passes in this check"
        elif b.verdict in (Verdict.FAIL, Verdict.NEEDS_REVIEW):
            change = "Still needs attention" if a.verdict in (Verdict.FAIL, Verdict.NEEDS_REVIEW) else "New attention needed"
        else:
            change = "Unchanged result"
        rows.append({"rule_id": rule_id, "title": (b or a).title, "change": change, "before": a, "after": b})
    return {"before": before, "after": after, "rows": rows, "same_rulebook": same_rules,
            "software_versions": software_versions,
            "coverage_changed": set(old) != set(new) or {k for k, f in old.items() if f.verdict == Verdict.NOT_EVALUABLE} != {k for k, f in new.items() if f.verdict == Verdict.NOT_EVALUABLE},
            "counts": dict(Counter(r["change"] for r in rows))}
