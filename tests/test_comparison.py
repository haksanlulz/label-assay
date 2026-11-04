import json

import pytest
from fastapi.testclient import TestClient

from label_assay.domain.models import Finding, LabelReport, Verdict
from label_assay.web.app import app
from label_assay.web.batch import BatchItem
from label_assay.web.comparison import compare_reports, read_saved_report


def saved(verdict, version="v1", rule="warning"):
    return {"filename": "label.png", "checked_at": "2026-11-04T00:00:00Z",
            "report": LabelReport(verdict=verdict, rulebook_version=version, findings=[
                Finding(rule_id=rule, title="Warning", citation="27 CFR", verdict=verdict, detail="Evidence")])}


def export(item):
    return json.dumps({"schema_version": 1, "items": [{**item, "status": "done", "report": item["report"].model_dump(mode="json")}]}).encode()


def test_only_same_rulebook_resolves_and_removed_rule_never_resolves():
    assert compare_reports(saved(Verdict.FAIL), saved(Verdict.PASS))["rows"][0]["change"] == "Resolved in this check"
    changed = compare_reports(saved(Verdict.FAIL), saved(Verdict.PASS, "v2"))
    assert not changed["same_rulebook"]
    assert "Resolved in this check" not in changed["counts"]
    removed = compare_reports(saved(Verdict.FAIL), saved(Verdict.PASS, rule="other"))
    assert removed["coverage_changed"]
    assert removed["rows"][0]["change"] == "No longer checked"


def test_unchecked_is_not_resolved_and_pass_with_unchecked_stays_in_queue():
    diff = compare_reports(saved(Verdict.FAIL), saved(Verdict.NOT_EVALUABLE))
    assert diff["rows"][0]["change"] == "Now uncheckable"
    item = BatchItem("label.png", status="done", verdict="pass", report=saved(Verdict.NOT_EVALUABLE)["report"])
    assert item.unchecked and item.review_status == "not_evaluable"


def test_report_parser_rejects_unknown_schema_duplicate_rules_and_wrong_rows():
    with pytest.raises(ValueError):
        read_saved_report(b'{"schema_version":2}', 0)
    with pytest.raises(ValueError):
        read_saved_report(export(saved(Verdict.PASS)), -1)
    duplicate = saved(Verdict.PASS)
    duplicate["report"].findings *= 2
    with pytest.raises(ValueError, match="unique"):
        read_saved_report(export(duplicate), 0)


def test_comparison_http_preserves_evidence_and_escapes_upload_content():
    before, after = saved(Verdict.FAIL), saved(Verdict.PASS)
    before["filename"] = '<script>alert("x")</script>'
    response = TestClient(app).post("/compare", files={
        "before_file": ("old.json", export(before), "application/json"),
        "after_file": ("new.json", export(after), "application/json"),
    })
    assert response.status_code == 200
    assert "Resolved in this check" in response.text
    assert "2026-11-04T00:00:00Z" in response.text and "27 CFR" in response.text
    assert '&lt;script&gt;' in response.text and '<script>alert' not in response.text


def test_malformed_comparison_is_a_readable_error():
    response = TestClient(app).post("/compare", files={"before_file": ("a.json", b"not json"), "after_file": ("b.json", b"{}")})
    assert response.status_code == 422
    assert "valid LabelAssay report" in response.text
