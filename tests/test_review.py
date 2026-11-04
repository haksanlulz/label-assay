"""Review decisions bind to a report without rewriting its automated findings."""
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from label_assay.domain.review import make_review, validate_review
from label_assay.web.app import app


@pytest.fixture
def source():
    return {"filename": "sample.png", "checked_at": None, "report": {
        "verdict": "fail", "rulebook_version": "fixture-v1", "findings": [
            {"rule_id": "warning", "title": "Warning", "citation": "Fixture citation",
             "verdict": "fail", "detail": "Needs a correction.", "diff": []},
            {"rule_id": "bold", "title": "Bold", "citation": "Fixture citation",
             "verdict": "not_evaluable", "detail": "No image supplied.", "diff": []},
        ]}}


def decisions(source, choice="pending"):
    return [{"rule_id": f["rule_id"], "choice": choice, "note": "Checked against artwork"}
            for f in source["report"]["findings"]]


def test_review_preserves_report_and_pending_status(source):
    before = deepcopy(source)
    record = make_review(source, "Reviewer", decisions(source), "Second read needed")
    assert not record.complete
    assert record.source_report.model_dump(mode="json") == before
    assert source == before
    assert validate_review(record.model_dump(mode="json")) == record
    complete = make_review(source, "Reviewer", decisions(source, "request_correction"), "")
    assert complete.complete
    assert complete.source_report.report.verdict == "fail"
    assert complete.report_sha256 == record.report_sha256


@pytest.mark.parametrize("mutation", ["report", "digest", "missing", "duplicate", "unknown",
                                      "complete", "empty_reviewer", "extra", "empty_report"])
def test_inconsistent_or_malformed_record_is_rejected(source, mutation):
    data = make_review(source, "Reviewer", decisions(source), "").model_dump(mode="json")
    if mutation == "report":
        data["source_report"]["report"]["findings"][0]["detail"] = "Changed"
    elif mutation == "digest":
        data["report_sha256"] = "0" * 64
    elif mutation == "missing":
        data["decisions"].pop()
    elif mutation == "duplicate":
        data["decisions"][1] = data["decisions"][0]
    elif mutation == "unknown":
        data["decisions"][0]["choice"] = "approved"
    elif mutation == "complete":
        data["complete"] = True
    elif mutation == "empty_reviewer":
        data["reviewer"] = " "
    elif mutation == "extra":
        data["source_report"]["report"]["findings"][0]["approved"] = True
    else:
        data["source_report"]["report"]["findings"] = []
    with pytest.raises(ValueError):
        validate_review(data)


def test_routes_load_saved_row_export_without_models_and_escape_text(source):
    client = TestClient(app)  # no lifespan/model warmup
    source["filename"] = '<script>alert("x")</script>.png'
    export = {"schema_version": 1, "items": [{"status": "done", **source}]}
    assert client.get("/review").status_code == 200
    loaded = client.post("/review/load", files={"report_file": ("report.json", json.dumps(export))},
                         data={"report_index": "0"})
    assert loaded.status_code == 200
    assert "&lt;script&gt;" in loaded.text and '<script>alert("x")' not in loaded.text
    form = {"source_json": json.dumps(source), "reviewer": "Reviewer", "notes": "Follow up",
            "decision_0": "escalate", "note_0": "Needs specialist",
            "decision_1": "pending", "note_1": ""}
    response = client.post("/review/export", data=form)
    assert response.status_code == 200
    record = validate_review(response.json())
    assert not record.complete and record.source_report.report.verdict == "fail"
    assert response.headers["content-disposition"].endswith('"label-assay-review.json"')
    form["decision_0"] = "approved"
    rejected = client.post("/review/export", data=form)
    assert rejected.status_code == 422
    assert 'value="Reviewer"' in rejected.text and "Needs specialist" in rejected.text
    assert client.post("/review/load", files={"report_file": ("bad.json", "{}")}).status_code == 422
    assert client.post("/review/load", files={"report_file": ("large.json", b" " * (5*1024*1024+1))}).status_code == 413
