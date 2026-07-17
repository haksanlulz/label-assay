"""Web routes: the single-label flow, upload validation, and error handling.

The happy path injects a FixtureExtractor so it is deterministic and needs no
API key; OCR and the bold check still run on the real fixture image (offline).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fixture_corpus
from label_assay.extract.fixture import FixtureExtractor
from label_assay.web import app as webapp
from label_assay.web.service import ExtractionUnavailable
from synthetic_images import bomb_png

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
    assert resp.status_code == 503  # a monitor must not read this failure as success
    assert "Couldn't check the label" in resp.text


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
    assert "Checked in" in resp.text  # the measured time of the check is shown


def test_fail_page_renders_plain_language_badges_and_the_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The failure page is the tool's core reviewer artifact and was never
    # rendered through the web layer. OCR is stubbed with a faithful read of the
    # altered label so corroboration holds and the FAIL is deterministic.
    spec = next(s for s in fixture_corpus.corpus_specs() if s.defect == "warning_altered_text")
    image = fixture_corpus.fixture_path(spec).read_bytes()
    fixture = FixtureExtractor(
        {hashlib.sha256(image).hexdigest(): fixture_corpus.perfect_extraction(spec)}
    )
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: fixture)

    from label_assay.extract.ocr import OcrLine

    painted = (spec.painted_brand, spec.class_type, spec.alcohol_text, spec.net_contents, spec.warning_text)
    lines = [OcrLine(text, 0.95) for text in painted if text]
    monkeypatch.setattr("label_assay.web.service.read_lines", lambda _image: lines)

    resp = client.post(
        "/check",
        files={"image": (spec.filename, image, "image/png")},
        data={"brand_name": spec.filed_brand, "class_type": spec.class_type},
    )
    assert resp.status_code == 200
    assert "Needs correction" in resp.text
    # Badges use the same plain-language labels as the batch table, never the
    # raw enum vocabulary.
    assert 'badge--fail">Needs correction<' in resp.text
    assert ">fail<" not in resp.text
    assert ">not_evaluable<" not in resp.text
    # The word-level diff block renders for the altered warning.
    assert "Differences from the required text" in resp.text
    assert 'class="diff"' in resp.text


def test_batch_js_verdict_labels_match_the_server_map() -> None:
    # The single-label page and the batch table cannot share code across the
    # wire, so this pins their vocabularies to one owner (_VERDICT_LABEL).
    js = (Path(webapp.__file__).parent / "static" / "batch.js").read_text(encoding="utf-8")
    block = re.search(r"var LABELS = \{(.*?)\};", js, re.S)
    assert block, "batch.js LABELS map not found"
    js_labels = dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block.group(1)))
    for verdict, label in webapp._VERDICT_LABEL.items():
        assert js_labels[verdict.value] == label


def test_check_rejects_a_decompression_bomb_with_a_clean_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Under 5 MB compressed, ~144 MB decoded: the guards must reject it politely
    # instead of decoding it (which is how the single deployed machine dies).
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: object())
    resp = client.post(
        "/check",
        files={"image": ("big.png", bomb_png(8000, 6000), "image/png")},
        data={"brand_name": "X", "class_type": "Y"},
    )
    assert resp.status_code == 503
    assert "too large to process" in resp.text


def test_check_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # A slow check must not stall other requests: the route hands the pipeline
    # to a worker thread, exactly as the batch path does. Run inline, the stub's
    # sleep holds the loop and /health cannot answer until it ends.
    import httpx

    def slow_check_label(*args, **kwargs):
        time.sleep(0.8)
        raise ExtractionUnavailable("slow reader finished")

    monkeypatch.setattr(webapp, "check_label", slow_check_label)
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: object())
    monkeypatch.setattr(webapp, "_ocr_status", lambda: "ready")

    async def drive() -> tuple[int, float, int]:
        transport = httpx.ASGITransport(app=webapp.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
            started = time.perf_counter()
            check_task = asyncio.create_task(
                c.post(
                    "/check",
                    files={"image": ("l.png", png, "image/png")},
                    data={"brand_name": "X", "class_type": "Y"},
                )
            )
            await asyncio.sleep(0.1)  # let /check reach its worker thread
            health = await c.get("/health")
            health_done = time.perf_counter() - started
            check_resp = await check_task
            return health.status_code, health_done, check_resp.status_code

    health_status, health_done, check_status = asyncio.run(drive())
    assert health_status == 200
    assert check_status == 503  # the slow check still renders its clean error page
    assert health_done < 0.5, f"/health waited {health_done:.2f}s behind a single /check"
