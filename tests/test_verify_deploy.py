"""The deploy verifier's decision logic.

tools/verify_deploy.py gates the claim that the public URL is live and serving
this tree. These tests pin what it accepts, what it reports, and the anchors
that tie it to the rest of the repo: the result template it parses, the
manifest it picks its probe from, and the README's Live link.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
MANIFEST = TESTS / "fixtures" / "labels" / "manifest.csv"
RESULT_TEMPLATE = REPO / "src" / "label_assay" / "web" / "templates" / "result.html"


def _import_tool():
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import verify_deploy

    return verify_deploy


verify_deploy = _import_tool()


def _healthy(version: str = "abc123def456") -> dict[str, object]:
    return {
        "status": "ok",
        "version": "0.1.0",
        "rulebook_version": version,
        "rulebook_rules": 4,
        "ai_reader": "configured",
        "ocr": "ready",
        "instance": "test-machine",
    }


def test_matching_health_payload_raises_no_problems() -> None:
    assert verify_deploy.health_problems(_healthy(), "abc123def456") == []


def test_served_rulebook_from_another_tree_is_reported_with_both_hashes() -> None:
    problems = verify_deploy.health_problems(_healthy("stale00000ff"), "current0000a")
    assert len(problems) == 1
    assert "stale00000ff" in problems[0]
    assert "current0000a" in problems[0]


def test_degraded_subsystems_are_each_reported() -> None:
    payload = _healthy()
    payload["status"] = "error"
    payload["ocr"] = "failed: ImportError: onnxruntime"
    payload["ai_reader"] = "not-configured"
    problems = verify_deploy.health_problems(payload, "abc123def456")
    assert len(problems) == 3


def test_empty_health_payload_reports_every_expectation() -> None:
    assert len(verify_deploy.health_problems({}, "abc123def456")) == 4


def test_sample_route_gone_is_clean() -> None:
    assert verify_deploy.sample_problems(404) == []


def test_sample_route_still_answering_flags_a_stale_build() -> None:
    problems = verify_deploy.sample_problems(200)
    assert len(problems) == 1
    assert "before the removal" in problems[0]


def test_result_page_parser_anchor_exists_in_the_template() -> None:
    # The verifier reads the verdict out of the alert block; if the template
    # renames that class, the parser goes blind and this catches it.
    template = RESULT_TEMPLATE.read_text(encoding="utf-8")
    assert 'class="alert alert--{{ report.verdict.value }}"' in template
    assert "Checked in" in template


def test_pass_page_is_clean() -> None:
    page = '<div class="alert alert--pass"><p>Compliant</p></div>'
    assert verify_deploy.check_problems(200, page) == []


def test_fail_page_reports_comparator_drift() -> None:
    page = '<div class="alert alert--fail"><p>Needs correction</p></div>'
    problems = verify_deploy.check_problems(200, page)
    assert len(problems) == 1
    assert "known-compliant" in problems[0]


def test_review_page_is_not_claimed_live() -> None:
    page = '<div class="alert alert--needs_review"><p>Needs your review</p></div>'
    problems = verify_deploy.check_problems(200, page)
    assert len(problems) == 1
    assert "needs_review" in problems[0]


def test_non_result_page_is_reported() -> None:
    problems = verify_deploy.check_problems(200, "<html><body>an error page</body></html>")
    assert len(problems) == 1


def test_check_http_error_is_reported() -> None:
    problems = verify_deploy.check_problems(500, "")
    assert problems == ["POST /check returned 500, expected 200"]


def test_server_elapsed_parses_the_rendered_meta() -> None:
    assert verify_deploy.server_elapsed("<p>Checked in 3.2&nbsp;s.</p>") == 3.2
    assert verify_deploy.server_elapsed("<p>no timing here</p>") is None


def test_probe_prefers_the_light_palette_body_caps_label() -> None:
    rows = [
        {"filename": "a.png", "defect": "compliant", "expected_verdict": "pass", "notes": "palette=white"},
        {"filename": "b.png", "defect": "warning_body_caps", "expected_verdict": "pass", "notes": "palette=navy"},
        {"filename": "c.png", "defect": "warning_body_caps", "expected_verdict": "pass", "notes": "palette=white"},
    ]
    assert verify_deploy.pick_probe(rows)["filename"] == "c.png"


def test_probe_missing_from_manifest_is_an_error() -> None:
    rows = [{"filename": "a.png", "defect": "compliant", "expected_verdict": "pass", "notes": ""}]
    with pytest.raises(LookupError):
        verify_deploy.pick_probe(rows)


def test_probe_selection_against_the_committed_manifest() -> None:
    import csv

    with MANIFEST.open(newline="", encoding="utf-8") as handle:
        probe = verify_deploy.pick_probe(list(csv.DictReader(handle)))
    assert probe["defect"] == "warning_body_caps"
    assert probe["expected_verdict"] == "pass"
    assert "palette=white" in probe["notes"]
    assert (MANIFEST.parent / probe["filename"]).exists()


def test_default_base_url_matches_the_readme_live_link() -> None:
    # verify_deploy.DEPLOY_URL owns the public URL; the README's Live link must agree.
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert f"**Live:** {verify_deploy.default_base_url()}" in readme


def test_verifier_accepts_the_app_it_ships_with() -> None:
    # The identity probes must hold against this tree's own app, or a correct
    # deploy of this tree would be reported as stale. ai_reader and ocr state
    # depend on the host, so only the identity checks are asserted.
    from fastapi.testclient import TestClient

    from label_assay.rulebook.loader import load_rulebook
    from label_assay.web.app import app

    client = TestClient(app)
    payload = client.get("/health").json()
    problems = verify_deploy.health_problems(payload, load_rulebook().version)
    assert not [p for p in problems if "rulebook_version" in p]
    assert verify_deploy.sample_problems(client.get("/sample").status_code) == []
