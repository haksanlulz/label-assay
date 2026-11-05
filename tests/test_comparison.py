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


def export(item, app_version=None):
    return json.dumps({"schema_version": 1, "app_version": app_version, "items": [
        {**item, "status": "done", "report": item["report"].model_dump(mode="json")}]}).encode()


def test_pass_transition_is_neutral_and_removed_rule_is_not_a_pass():
    assert compare_reports(saved(Verdict.FAIL), saved(Verdict.PASS))["rows"][0]["change"] == "Now passes in this check"
    changed = compare_reports(saved(Verdict.FAIL), saved(Verdict.PASS, "v2"))
    assert not changed["same_rulebook"]
    assert changed["software_versions"] == "unknown"
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
        "before_file": ("old.json", export(before, "0.2.0"), "application/json"),
        "after_file": ("new.json", export(after, "0.2.0"), "application/json"),
    })
    assert response.status_code == 200
    assert "Now passes in this check" in response.text
    assert "Software version unknown" not in response.text
    assert "2026-11-04T00:00:00Z" in response.text and "27 CFR" in response.text
    assert '&lt;script&gt;' in response.text and '<script>alert' not in response.text


def test_different_or_missing_app_versions_do_not_imply_equivalent_checking_software():
    old = read_saved_report(export(saved(Verdict.FAIL), "0.1.0"), 0)
    new = read_saved_report(export(saved(Verdict.PASS), "0.2.0"), 0)
    comparison = compare_reports(old, new)
    assert comparison["same_rulebook"]
    assert comparison["software_versions"] == "different_declared"
    assert comparison["rows"][0]["change"] == "Now passes in this check"
    client = TestClient(app)
    different = client.post("/compare", files={
        "before_file": ("old.json", export(saved(Verdict.FAIL), "0.1.0")),
        "after_file": ("new.json", export(saved(Verdict.PASS), "0.2.0")),
    })
    assert "Software version labels differ" in different.text
    older = client.post("/compare", files={
        "before_file": ("old.json", export(saved(Verdict.FAIL))),
        "after_file": ("new.json", export(saved(Verdict.PASS), "0.2.0")),
    })
    assert "Software version unknown" in older.text
    assert "Unknown" in older.text


def test_malformed_comparison_is_a_readable_error():
    response = TestClient(app).post("/compare", files={"before_file": ("a.json", b"not json"), "after_file": ("b.json", b"{}")})
    assert response.status_code == 422
    assert "valid LabelAssay report" in response.text


def test_blank_rulebook_is_unknown_not_matching():
    client = TestClient(app)
    response = client.post("/compare", files={
        "before_file": ("old.json", export(saved(Verdict.FAIL, ""), "0.2.0")),
        "after_file": ("new.json", export(saved(Verdict.PASS, " "), "0.2.0")),
    })
    assert response.status_code == 200
    assert "Rulebook version unknown" in response.text
    assert "Rulebook changed" not in response.text
