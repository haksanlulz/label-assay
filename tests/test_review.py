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
    assert record.source_report.model_dump(mode="json") == {**before, "app_version": None}
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


def test_versioned_provenance_and_legacy_digest(source):
    from label_assay.domain.review import ReviewRecordV1, ReviewSourceV1, report_digest

    old_source = ReviewSourceV1.model_validate(source)
    legacy = ReviewRecordV1(source_report=old_source, report_sha256=report_digest(old_source),
                            reviewer="Reviewer", reviewed_at="2026-11-04T00:00:00Z",
                            decisions=decisions(source), notes="", complete=False)
    assert validate_review(legacy.model_dump(mode="json")) == legacy
    one = make_review({**source, "app_version": "0.1"}, "Reviewer", decisions(source), "")
    two = make_review({**source, "app_version": "0.2"}, "Reviewer", decisions(source), "")
    assert one.schema_version == 2 and one.report_sha256 != two.report_sha256
    assert validate_review(one.model_dump(mode="json")) == one


def test_web_review_retains_export_version(source):
    from html import unescape
    import re
    client = TestClient(app)
    export = {"schema_version": 1, "app_version": "0.2.0", "items": [{"status": "done", **source}]}
    loaded = client.post("/review/load", files={"report_file": ("report.json", json.dumps(export))})
    hidden = unescape(re.search(r'name="source_json" value="([^"]*)"', loaded.text).group(1))
    data = {"source_json": hidden, "reviewer": "Reviewer", "notes": "",
            "decision_0": "pending", "note_0": "", "decision_1": "pending", "note_1": ""}
    record = client.post("/review/export", data=data).json()
    assert record["schema_version"] == 2
    assert record["source_report"]["app_version"] == "0.2.0"
    validate_review(record)


def test_large_unicode_http_download_verifies_in_both_json_encodings(source, tmp_path):
    """Accepted source and max-length notes remain portable without losing text."""
    from html import unescape
    from pathlib import Path
    import re
    import subprocess
    import sys

    from label_assay.domain.review import MAX_REVIEW_RECORD_BYTES, MAX_REVIEW_SOURCE_BYTES

    source["report"]["findings"] = [
        {**source["report"]["findings"][0], "rule_id": f"rule-{index}",
         "detail": "\U0001f600" * 2560}
        for index in range(500)
    ]
    export = {"schema_version": 1, "items": [{"status": "done", **source}]}
    uploaded = json.dumps(export, ensure_ascii=False).encode("utf-8")
    assert MAX_REVIEW_SOURCE_BYTES * 0.98 < len(uploaded) <= MAX_REVIEW_SOURCE_BYTES
    client = TestClient(app)
    loaded = client.post("/review/load", files={"report_file": ("report.json", uploaded)})
    assert loaded.status_code == 200
    hidden = unescape(re.search(r'name="source_json" value="([^"]*)"', loaded.text).group(1))
    # Include surrogate-pair expansion, JSON control escapes and a full overall note.
    note = "\U0001f600" * 1998 + "\x00\n"
    form = {"source_json": hidden, "reviewer": "Reviewer", "notes": "\x00" * 4000}
    for index in range(500):
        form.update({f"decision_{index}": "agree", f"note_{index}": note})
    response = client.post("/review/export", data=form)
    assert response.status_code == 200
    assert 8 * 1024 * 1024 < len(response.content) <= MAX_REVIEW_RECORD_BYTES
    record = response.json()
    assert record["source_report"]["report"]["findings"] == source["report"]["findings"]
    assert all(decision["note"] == note for decision in record["decisions"])
    assert record["notes"] == form["notes"]
    runner = Path(__file__).parents[1] / "tools/verify_review.py"
    for name, content in [
        ("utf8", response.content),
        ("escaped", json.dumps(record, ensure_ascii=True).encode("utf-8")),
    ]:
        assert len(content) <= MAX_REVIEW_RECORD_BYTES
        path = tmp_path / f"{name}.json"
        path.write_bytes(content)
        verified = subprocess.run([sys.executable, str(runner), str(path)],
                                  capture_output=True, text=True)
        assert verified.returncode == 0, verified.stderr
        assert "500 decisions; complete" in verified.stdout


def test_verifier_refuses_files_beyond_shared_byte_limit(tmp_path):
    from pathlib import Path
    import subprocess
    import sys

    from label_assay.domain.review import MAX_REVIEW_RECORD_BYTES

    path = tmp_path / "oversize.json"
    with path.open("wb") as stream:
        stream.seek(MAX_REVIEW_RECORD_BYTES)
        stream.write(b" ")
    runner = Path(__file__).parents[1] / "tools/verify_review.py"
    verified = subprocess.run([sys.executable, str(runner), str(path)],
                              capture_output=True, text=True)
    assert verified.returncode == 1
    assert "exceeds 32 MiB" in verified.stderr
