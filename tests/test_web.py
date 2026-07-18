"""Web routes: the single-label flow, upload validation, and error handling.

The happy path injects a FixtureExtractor so it is deterministic and needs no
API key; OCR and the bold check still run on the real fixture image (offline).
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import fixture_corpus
from label_assay.config import Settings
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.fixture import FixtureExtractor, fixture_key
from label_assay.web import app as webapp
from label_assay.web.service import ExtractionUnavailable
from synthetic_images import bomb_png

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)
client = TestClient(webapp.app)

# Elements HTML5 defines as void: they never take an end tag, so they must not
# stay on the open-ancestor stack.
_VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta source track wbr".split()
)


class _ElementCollector(HTMLParser):
    """Collects every element as (tag, attributes, ancestor tags), so structural
    assertions run against parsed markup — a page whose prose merely mentions a
    tag cannot satisfy them."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, dict[str, str | None], tuple[str, ...]]] = []
        self._open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.found.append((tag, dict(attrs), tuple(self._open)))
        if tag not in _VOID_TAGS:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._open:
            return
        while self._open and self._open.pop() != tag:
            pass


def _elements(html: str) -> list[tuple[str, dict[str, str | None], tuple[str, ...]]]:
    parser = _ElementCollector()
    parser.feed(html)
    parser.close()
    return parser.found


def test_index_renders_the_form() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Check a Label" in resp.text
    assert 'action="/check"' in resp.text
    assert 'name="fanciful_name"' in resp.text  # the optional fanciful-name input
    # The rotation control is a plain select; every control on the form works
    # with scripting disabled.
    assert '<select id="rotation" name="rotation">' in resp.text
    assert "Label image is rotated" in resp.text
    for value in ("0", "90", "180", "270"):
        assert f'value="{value}"' in resp.text
    # Deliberately narrowed invariant (was: zero script on this page): the page
    # carries exactly one script, the external submit guard — a progressive
    # enhancement. No script is required: scripting off leaves the form fully
    # functional, with the button staying enabled.
    scripts = [attrs for tag, attrs, _ in _elements(resp.text) if tag == "script"]
    assert [attrs.get("src") for attrs in scripts] == ["/static/submit-guard.js"]


def test_check_passes_the_fanciful_name_through_and_defaults_it_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The route must hand the form field to the engine's Application; a stub in
    # place of check_label captures exactly what it was given.
    captured: list = []

    def capture_check_label(data, application, *, extractor=None, budget=None, rotation=0):
        captured.append(application)
        raise ExtractionUnavailable("captured")

    monkeypatch.setattr(webapp, "check_label", capture_check_label)
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: object())
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    resp = client.post(
        "/check",
        files={"image": ("l.png", png, "image/png")},
        data={
            "brand_name": "Earthbound Beer",
            "class_type": "Beer",
            "fanciful_name": " Yellow Card Pils ",
        },
    )
    assert resp.status_code == 503
    assert captured[0].brand_name == "Earthbound Beer"
    assert captured[0].fanciful_name == "Yellow Card Pils"

    resp = client.post(  # omitted entirely: an older form or script still works
        "/check",
        files={"image": ("l.png", png, "image/png")},
        data={"brand_name": "X", "class_type": "Y"},
    )
    assert resp.status_code == 503
    assert captured[1].fanciful_name == ""


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
    assert "Couldn't Check the Label" in resp.text


def test_check_happy_path_renders_a_cited_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    image = FIXTURE.read_bytes()
    fixture = FixtureExtractor(
        {fixture_key(image): fixture_corpus.perfect_extraction(SPEC)}
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
        {fixture_key(image): fixture_corpus.perfect_extraction(spec)}
    )
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: fixture)

    from label_assay.extract.ocr import OcrLine

    painted = (spec.painted_brand, spec.class_type, spec.alcohol_text, spec.net_contents, spec.warning_text)
    lines = [OcrLine(text, 0.95) for text in painted if text]
    monkeypatch.setattr(
        "label_assay.web.service.read_lines",
        lambda _image, background=False, rotation=0: lines,
    )

    resp = client.post(
        "/check",
        files={"image": (spec.filename, image, "image/png")},
        data={"brand_name": spec.filed_brand, "class_type": spec.class_type},
    )
    assert resp.status_code == 200
    assert "Needs Correction" in resp.text
    # Badges use the same plain-language labels as the batch table, never the
    # raw enum vocabulary.
    assert 'badge--fail">Needs correction<' in resp.text
    assert ">fail<" not in resp.text
    assert ">not_evaluable<" not in resp.text
    # The word-level diff block renders for the altered warning.
    assert "Differences from the required text" in resp.text
    assert 'class="diff"' in resp.text


def _read_field(text: str | None) -> ExtractedField:
    return ExtractedField(verbatim=text, found=text is not None, value=text)


def _read_extraction(**overrides: ExtractedField) -> Extraction:
    fields: dict[str, ExtractedField] = {
        "brand_name": _read_field("Old Tom Gin"),
        "class_type": _read_field("London Dry Gin"),
        "alcohol_content": _read_field("45% ALC./VOL. (90 PROOF)"),
        "net_contents": _read_field("750 ML"),
        "government_warning": _read_field("GOVERNMENT WARNING: shortened for the test."),
    }
    fields.update(overrides)
    return Extraction(**fields)


class _FixedExtractor:
    """Returns one prepared extraction for any image."""

    def __init__(self, extraction: Extraction) -> None:
        self.extraction = extraction

    def extract(self, image: bytes) -> Extraction:
        return self.extraction


def _post_check_with(
    monkeypatch: pytest.MonkeyPatch, extraction: Extraction, image: bytes | None = None
):
    """One /check round-trip with a stubbed reader and a silent OCR pass, so
    the page under test renders exactly the given extraction."""
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: _FixedExtractor(extraction))
    monkeypatch.setattr(
        "label_assay.web.service.read_lines", lambda _image, background=False, rotation=0: []
    )
    if image is None:
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
        image = buffer.getvalue()
    return client.post(
        "/check",
        files={"image": ("label.png", image, "image/png")},
        data={"brand_name": "Old Tom Gin", "class_type": "Gin"},
    )


def test_result_page_reads_back_what_the_reader_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    # The read-back section shows the reviewer each field's verbatim text as the
    # reader returned it, and the warning as presence (its wording is judged in
    # the findings, not here).
    resp = _post_check_with(monkeypatch, _read_extraction())
    assert resp.status_code == 200
    assert "What Was Read From the Label" in resp.text
    assert "&ldquo;Old Tom Gin&rdquo;" in resp.text
    assert "&ldquo;London Dry Gin&rdquo;" in resp.text
    assert "&ldquo;45% ALC./VOL. (90 PROOF)&rdquo;" in resp.text
    assert "&ldquo;750 ML&rdquo;" in resp.text
    assert "Present; judged in the findings above." in resp.text
    assert "Not found on the label." not in resp.text  # every field was read
    # The reference-only note about unchecked fields is on the page.
    assert "read from the label, not judged" in resp.text


def test_result_page_marks_absent_fields_as_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    absent = ExtractedField(verbatim=None, found=False, value=None)
    resp = _post_check_with(
        monkeypatch, _read_extraction(net_contents=absent, government_warning=absent)
    )
    assert resp.status_code == 200
    assert resp.text.count("Not found on the label.") == 2
    assert "Present; judged" not in resp.text


def test_result_page_escapes_hostile_verbatim_text(monkeypatch: pytest.MonkeyPatch) -> None:
    # A label can print anything, so the reader's quoted text is data, never
    # markup; autoescape must hold on this page.
    hostile = ExtractedField(verbatim="<script>alert('x')</script>", found=True, value="x")
    resp = _post_check_with(monkeypatch, _read_extraction(brand_name=hostile))
    assert resp.status_code == 200
    assert "&lt;script&gt;" in resp.text
    assert "<script>" not in resp.text


def _preview_image_from(html: str) -> Image.Image:
    """Decode the page's embedded preview back into pixels, so the assertions
    run against what the browser would actually render."""
    match = re.search(r'src="data:image/jpeg;base64,([^"]+)"', html)
    assert match, "no JPEG data URI found on the page"
    return Image.open(io.BytesIO(base64.b64decode(match.group(1))))


def test_result_page_offers_the_upload_back_as_a_collapsed_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Native <details>/<summary>: collapsed by default, keyboard-accessible,
    # zero script. The image itself is a data: URI — nothing was stored.
    resp = _post_check_with(monkeypatch, _read_extraction())
    assert resp.status_code == 200
    assert "<details" in resp.text
    assert "Show the label image you uploaded" in resp.text
    assert "data:image/jpeg;base64," in resp.text
    img = _preview_image_from(resp.text)
    assert img.format == "JPEG"


def test_preview_is_genuinely_downscaled_not_the_original_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An oversized upload must come back as a bounded preview: decode the data
    # URI and measure it, rather than trusting the encoder's word.
    buffer = io.BytesIO()
    Image.new("RGB", (3000, 2000), "white").save(buffer, format="PNG")
    resp = _post_check_with(monkeypatch, _read_extraction(), image=buffer.getvalue())
    assert resp.status_code == 200
    img = _preview_image_from(resp.text)
    assert max(img.size) <= 1200
    assert img.size[0] > img.size[1]  # aspect kept: wide in, wide out


def test_preview_encode_failure_never_costs_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The preview is a convenience; if its encoder breaks, the page must render
    # complete without the section, not 500 over an image the check already read.
    def boom(_image: bytes, max_edge: int = 1200) -> bytes:
        raise ValueError("re-encode failed")

    monkeypatch.setattr(webapp, "preview_jpeg", boom)
    resp = _post_check_with(monkeypatch, _read_extraction())
    assert resp.status_code == 200
    assert "What Was Read From the Label" in resp.text  # the verdict page rendered
    assert "<details" not in resp.text
    assert "Show the label image you uploaded" not in resp.text
    assert "data:image/jpeg" not in resp.text


def test_result_page_carries_no_client_script(monkeypatch: pytest.MonkeyPatch) -> None:
    # The result page stays script-free: the submit guard belongs to the upload
    # forms and batch.js to the batch pages; rendering a verdict needs neither,
    # and the preview must not change that.
    resp = _post_check_with(monkeypatch, _read_extraction())
    assert resp.status_code == 200
    assert "<script" not in resp.text.lower()


@pytest.mark.parametrize(
    ("path", "busy_label", "note"),
    [
        ("/", "Checking…", "usually a few seconds"),
        ("/batch", "Uploading…", "large drops take a moment"),
    ],
)
def test_upload_forms_carry_the_submit_guard_and_its_progress_strip(
    path: str, busy_label: str, note: str
) -> None:
    # Both upload forms load the external submit guard (the CSP has no
    # 'unsafe-inline', so an inline handler could not run) and ship the
    # progress strip it reveals: a role="status" element inside the form,
    # hidden until submit, whose one-line note a screen reader announces once
    # when shown. The busy wording rides on the button itself so each form
    # names its own slow part — checking the label, or spooling the upload.
    page = client.get(path)
    assert page.status_code == 200
    elements = _elements(page.text)

    scripts = [attrs for tag, attrs, _ in elements if tag == "script"]
    assert [attrs.get("src") for attrs in scripts] == ["/static/submit-guard.js"]
    assert "defer" in scripts[0]

    buttons = [
        (attrs, ancestors)
        for tag, attrs, ancestors in elements
        if tag == "button" and attrs.get("type") == "submit"
    ]
    assert len(buttons) == 1
    button_attrs, button_ancestors = buttons[0]
    assert button_attrs.get("data-busy-label") == busy_label
    assert "form" in button_ancestors

    statuses = [
        (tag, attrs, ancestors)
        for tag, attrs, ancestors in elements
        if attrs.get("role") == "status"
    ]
    assert len(statuses) == 1
    _tag, status_attrs, status_ancestors = statuses[0]
    assert "progress" in (status_attrs.get("class") or "").split()
    assert "form" in status_ancestors  # the form's busy class is what reveals it

    fills = [
        ancestors
        for _tag, attrs, ancestors in elements
        if "progress__fill" in (attrs.get("class") or "").split()
    ]
    assert len(fills) == 1
    assert "form" in fills[0]
    assert note in page.text


def test_submit_guard_is_served_and_stays_within_the_csp() -> None:
    # The guard must exist as a served static file (script-src 'self' is the
    # only way any script runs), must never write element styles — style-src
    # 'self' bans style= attributes, so presentation happens by toggling
    # classes app.css owns — and must register pageshow, so a back/forward-
    # cache restore re-enables the button instead of stranding a dead form.
    assert client.get("/static/submit-guard.js").status_code == 200
    js = (Path(webapp.__file__).parent / "static" / "submit-guard.js").read_text(encoding="utf-8")
    assert "style." not in js
    assert "pageshow" in js
    # The guard's load-bearing semantics, pinned at source level (no JS runtime
    # in this suite, so string pins on the exact statements are the falsifiable
    # check available): the double-submit protection is the disable assignment,
    # the busy affordance is the label swap + class toggle, and the reset path
    # must flip the same switches back.
    assert "button.disabled = true" in js
    assert "button.disabled = false" in js
    assert "button.dataset.busyLabel" in js
    assert 'classList.add("is-busy")' in js
    assert 'classList.remove("is-busy")' in js


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


def test_startup_warm_fires_exactly_once_when_a_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The lifespan fires one budget-accounted warm extraction so the first user
    # request never pays the provider cold start. Stubbed at check_label: the
    # test pins the wiring (once, real budget, background priority), not the
    # network call.
    calls: list[dict] = []
    sentinel = object()

    def capture(data, application, *, extractor, budget=None, background=False):
        calls.append({"extractor": extractor, "budget": budget, "background": background})
        return None

    monkeypatch.setattr(webapp, "_WARM_ON_STARTUP", True)
    monkeypatch.setattr(webapp, "get_settings", lambda: Settings(anthropic_api_key="test-key"))
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: sentinel)
    monkeypatch.setattr(webapp, "check_label", capture)
    monkeypatch.setattr(webapp, "_ocr_status", lambda: "ready")

    with TestClient(webapp.app):
        deadline = time.time() + 5
        while not calls and time.time() < deadline:
            time.sleep(0.01)  # the warm-up is fire-and-forget; give the loop a beat
        time.sleep(0.05)  # room for a hypothetical second call to surface

    assert len(calls) == 1, f"warm extraction ran {len(calls)} times"
    assert calls[0]["extractor"] is sentinel
    assert calls[0]["budget"] is webapp._BUDGET  # accounted against the real daily budget
    assert calls[0]["background"] is True  # a user's first click still outranks the warm-up


def test_startup_warm_is_skipped_when_no_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list = []
    monkeypatch.setattr(webapp, "_WARM_ON_STARTUP", True)
    monkeypatch.setattr(webapp, "get_settings", lambda: Settings(anthropic_api_key=None))
    monkeypatch.setattr(webapp, "check_label", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(webapp, "_ocr_status", lambda: "ready")

    with TestClient(webapp.app):
        time.sleep(0.1)

    assert calls == []


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


_EXPECTED_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


def test_security_headers_on_a_normal_response() -> None:
    # Every response carries the hardening header set. The CSP stays strict
    # because the single-label path needs no inline script or style and every
    # asset is same-origin; the result page's data: image preview is the one
    # non-'self' source allowed.
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-security-policy"] == _EXPECTED_CSP
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert resp.headers["strict-transport-security"] == "max-age=31536000"


def test_batch_data_json_endpoint_carries_nosniff() -> None:
    # The /batch/{id}/data body reflects the uploaded filename verbatim; the
    # rendered table escapes it, and nosniff is the belt that stops a content-
    # sniffing client from ever reading the JSON body as markup.
    resp = client.get("/batch/deadbeefdead/data")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_index_carries_no_inline_style_so_the_csp_stays_strict() -> None:
    # The one inline style moved to app.css; a strict style-src 'self' (no
    # 'unsafe-inline') holds only while the rendered pages carry no style=
    # attribute.
    resp = client.get("/")
    assert resp.status_code == 200
    assert "style=" not in resp.text
    assert 'class="batch-cta"' in resp.text


def test_upload_surfaces_disclose_the_third_party_data_flow() -> None:
    # The uploader is told, before submitting, that the image is sent to a
    # third-party API, that camera metadata is stripped first (a claim only
    # allowed on the page because every egress path re-encodes —
    # extract/images.downscale_for_vision never emits the original bytes), and
    # that the image is not stored. The pins guard the facts, not the phrasing.
    index = client.get("/")
    assert "Anthropic" in index.text and "sent to" in index.text
    assert "metadata" in index.text and "removed" in index.text
    assert "not stored" in index.text
    batch = client.get("/batch")
    assert "Anthropic" in batch.text and "sent to" in batch.text
    assert "metadata" in batch.text and "removed" in batch.text
    assert "not stored" in batch.text


def test_oversized_declared_body_is_refused_with_a_plain_413() -> None:
    # The ceiling reads the declared Content-Length and answers before any of
    # the body streams; the body is a clean plain-text sentence, not a trace,
    # and the response still carries the hardening header set (the headers
    # middleware wraps the ceiling).
    resp = client.post(
        "/batch", headers={"Content-Length": str(webapp._MAX_REQUEST_BYTES + 1)}
    )
    assert resp.status_code == 413
    assert resp.headers["content-type"].startswith("text/plain")
    assert "Split the batch" in resp.text
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-security-policy"] == _EXPECTED_CSP


def test_declared_body_at_the_ceiling_passes_through_to_the_app() -> None:
    # Exactly at the ceiling is not over it: the request reaches the route,
    # whose own parsing answers (an empty body is a 422, not a 413).
    resp = client.post(
        "/batch", headers={"Content-Length": str(webapp._MAX_REQUEST_BYTES)}
    )
    assert resp.status_code != 413


def test_requests_without_content_length_are_not_refused_by_the_ceiling() -> None:
    # No declared length means the ceiling abstains; the multipart caps and the
    # batch total-bytes guard still bound what the body can deliver.
    resp = client.get("/health")
    assert resp.status_code == 200


def test_batch_404_copy_states_the_retention_policy() -> None:
    # The store evicts finished jobs beyond the most recent 50, so the page
    # says exactly that — the copy must stay literally true of the store.
    resp = client.get("/batch/no-such-job")
    assert resp.status_code == 404
    assert "most recent" in resp.text


def test_batch_js_clamps_the_badge_class_to_a_fixed_allowlist() -> None:
    # The badge modifier is interpolated into a class attribute via innerHTML, so
    # it must be clamped to a fixed set — esc() does not neutralize an attribute
    # breakout. Pin that the allowlist exists, that badgeClass clamps through it,
    # and that it covers every verdict the server can emit (so real rows still
    # render while an off-contract value cannot reach the attribute).
    js = (Path(webapp.__file__).parent / "static" / "batch.js").read_text(encoding="utf-8")
    block = re.search(r"var BADGE_CLASSES = \[(.*?)\];", js)
    assert block, "batch.js BADGE_CLASSES allowlist not found"
    allowed = set(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert "BADGE_CLASSES.indexOf" in js  # clamps, never returns the raw status
    for verdict in webapp._VERDICT_LABEL:
        assert verdict.value in allowed
