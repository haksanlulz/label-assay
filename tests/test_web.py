"""Web routes: the single-label flow, upload validation, and error handling.

The happy path injects a FixtureExtractor so it is deterministic and needs no
API key; OCR and the bold check still run on the real sample image (offline).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.fixture import FixtureExtractor
from label_assay.rulebook.loader import load_rulebook
from label_assay.web import app as webapp
from label_assay.web.service import ExtractionUnavailable

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "bourbon_compliant.png"
client = TestClient(webapp.app)


def _f(text: str) -> ExtractedField:
    return ExtractedField(verbatim=text, found=True, value=text)


def _compliant_extraction() -> Extraction:
    warning = next(r for r in load_rulebook().rules if r.id == "health_warning_verbatim").match.reference
    return Extraction(
        brand_name=_f("OLD TOM DISTILLERY"),
        class_type=_f("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=_f("45% Alc./Vol. (90 Proof)"),
        net_contents=_f("750 mL"),
        government_warning=_f(warning),
    )


def test_index_renders_the_form() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Check a label" in resp.text
    assert 'action="/check"' in resp.text


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


@pytest.mark.skipif(not SAMPLE.exists(), reason="run samples/make_samples.py first")
def test_check_happy_path_renders_a_cited_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    image = SAMPLE.read_bytes()
    fixture = FixtureExtractor({hashlib.sha256(image).hexdigest(): _compliant_extraction()})
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: fixture)

    resp = client.post(
        "/check",
        files={"image": ("label.png", image, "image/png")},
        data={"brand_name": "Old Tom Distillery", "class_type": "Kentucky Straight Bourbon Whiskey"},
    )
    assert resp.status_code == 200
    assert "Compliant" in resp.text
    assert "27 CFR 16.21" in resp.text  # a citation is shown to the reviewer
