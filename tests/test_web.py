"""Web routes: the single-label flow, upload validation, and error handling.

The happy path injects a FixtureExtractor so it is deterministic and needs no
API key; OCR and the bold check still run on the real fixture image (offline).
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

import fixture_corpus
from label_assay.extract.fixture import FixtureExtractor
from label_assay.web import app as webapp
from label_assay.web.service import ExtractionUnavailable

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)
client = TestClient(webapp.app)


def test_index_renders_the_form() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Check a label" in resp.text
    assert 'action="/check"' in resp.text


def test_sample_route_is_gone() -> None:
    # The built-in demo label was removed; the route must not linger.
    assert client.get("/sample").status_code == 404


def test_static_css_is_served() -> None:
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert "alert--fail" in resp.text


def test_check_rejects_non_image() -> None:
    resp = client.post(
        "/check",
        files={"image": ("x.txt", b"not an image at all", "text/plain")},
        data={"brand_name": "X", "class_type": "Y"},
    )
    assert resp.status_code == 415
    assert "PNG or JPEG" in resp.text


def test_check_shows_clean_error_when_reader_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_settings):
        raise ExtractionUnavailable("The label reader is not configured on this server.")

    monkeypatch.setattr(webapp, "default_extractor", unavailable)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    resp = client.post(
        "/check",
        files={"image": ("l.png", png, "image/png")},
        data={"brand_name": "X", "class_type": "Y"},
    )
    assert resp.status_code == 200
    assert "Couldn't check the label" in resp.text


@pytest.mark.skipif(not FIXTURE.exists(), reason="run tools/make_test_labels.py first")
def test_check_happy_path_renders_a_cited_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    image = FIXTURE.read_bytes()
    fixture = FixtureExtractor(
        {hashlib.sha256(image).hexdigest(): fixture_corpus.perfect_extraction(SPEC)}
    )
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: fixture)

    resp = client.post(
        "/check",
        files={"image": (SPEC.filename, image, "image/png")},
        data={"brand_name": SPEC.filed_brand, "class_type": SPEC.class_type},
    )
    assert resp.status_code == 200
    assert "Compliant" in resp.text
    assert "27 CFR 16.21" in resp.text  # a citation is shown to the reviewer
