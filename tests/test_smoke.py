"""The app serves and the rulebook loads. Exercises the full ASGI app via the
test client — this is real end-to-end proof the server works, not a mock."""

from __future__ import annotations

from fastapi.testclient import TestClient

from label_assay.web.app import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    # Assert the subsystem signals, not just the headline: a hardcoded "ok"
    # over a failed OCR probe is exactly the bug this test must catch.
    assert body["ocr"] == "ready"
    assert body["status"] == "ok"
    assert body["ai_reader"] in {"configured", "not-configured"}
    assert body["rulebook_rules"] >= 1


def test_health_degrades_when_ocr_probe_fails(monkeypatch) -> None:
    import label_assay.web.app as webapp

    monkeypatch.setattr(webapp, "_ocr_status", lambda: "failed: ImportError: cv2")
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["ocr"].startswith("failed:")


def test_index_renders() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LabelAssay" in resp.text
