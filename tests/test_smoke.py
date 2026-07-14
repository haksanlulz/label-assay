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
    assert body["status"] == "ok"
    assert body["rulebook_rules"] >= 1


def test_index_renders() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LabelAssay" in resp.text
