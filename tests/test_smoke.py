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

    # The probe reports a generic "failed" (no internal detail); the health
    # headline must go degraded off it.
    monkeypatch.setattr(webapp, "_ocr_status", lambda: "failed")
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["ocr"] == "failed"


def test_ocr_probe_failure_does_not_leak_internals_to_health(monkeypatch) -> None:
    # /health is unauthenticated (an uptime check curls it publicly), so a broken
    # OCR probe must not surface the exception text — which can name an absolute
    # path to a model file under site-packages — in the payload. The detail stays
    # in the server log; the public status is a bare "failed".
    import label_assay.web.app as webapp

    def boom(_image, **_kwargs):
        raise RuntimeError(
            "[ONNXRuntimeError] : 6 : Load model from /app/.venv/lib/python3.12/"
            "site-packages/rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx failed"
        )

    monkeypatch.setattr(webapp, "_OCR_READY", None)  # force a re-probe past the cache
    monkeypatch.setattr("label_assay.extract.ocr.read_lines", boom)

    status = webapp._ocr_status()
    assert status == "failed"
    for leaked in ("onnx", "site-packages", "RuntimeError", "/app/"):
        assert leaked.lower() not in status.lower()


def test_index_renders() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LabelAssay" in resp.text
