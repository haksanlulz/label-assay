"""Rotation handling on both paths. EXIF-oriented uploads decode upright for
every consumer. The interactive path takes the operator's stated rotation from
the form's select, straightens the raster once at the top of the check, and
never pays a retry pass — its latency target is unconditional. The batch path
opts in (checkbox, on by default) to the bounded rotation retry that recovers
a warning printed sideways.

Real registry composites (tests/fixtures/cola/README.md) print the mandated
warning rotated 90 degrees along an edge; the vision model reads rotated text
natively, but the OCR channel does not, so without recovery the corroboration
gate holds every such label for review. The retry's contract, pinned here: at
most three extra OCR passes, only when the warning was not found upright, and
only when the caller opted in via ``recover_rotation``. The select's contract,
also pinned: its value is how the image LOOKS to the person (degrees
clockwise), and the pixels come out upright under the matching
counter-clockwise transpose.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import fixture_corpus
from label_assay.domain.models import Application, Verdict
from label_assay.extract import ocr as ocrmod
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.images import transpose_image
from label_assay.extract.ocr import OcrLine
from label_assay.web import app as webapp
from label_assay.web import batch as batchmod
from label_assay.web import service
from label_assay.web.batch import create_job, run_job
from label_assay.web.service import _recover_rotated_warning, check_label

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)
REFERENCE = fixture_corpus.mandated_warning()
client = TestClient(webapp.app)

_TRANSPOSE = {
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


def _rotated_fixture(degrees_ccw: int) -> bytes:
    buffer = io.BytesIO()
    img = Image.open(io.BytesIO(FIXTURE.read_bytes()))
    img.transpose(_TRANSPOSE[degrees_ccw]).save(buffer, format="PNG")
    return buffer.getvalue()


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _PerfectExtractor:
    """The vision channel reads rotated text natively, so it is modeled as the
    perfect read of the fixture regardless of the image's orientation."""

    def extract(self, image: bytes) -> Extraction:
        return fixture_corpus.perfect_extraction(SPEC)


def _absent() -> ExtractedField:
    return ExtractedField(verbatim=None, found=False, value=None)


class _AbsentExtractor:
    def extract(self, image: bytes) -> Extraction:
        return Extraction(
            brand_name=_absent(),
            class_type=_absent(),
            alcohol_content=_absent(),
            net_contents=_absent(),
            government_warning=_absent(),
        )


def _counting_real_read(passes: list[int]):
    real_read_lines = service.read_lines

    def counting(image: bytes, *, background: bool = False, rotation: int = 0):
        passes.append(rotation)
        return real_read_lines(image, background=background, rotation=rotation)

    return counting


# --- The batch path's opt-in retry (recover_rotation=True) -------------------


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_rotated_label_recovers_its_warning_through_the_retry(
    degrees: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole service path with real OCR on a fully rotated label, opted in
    # as a batch item is: the retry finds the rotation that reads upright, the
    # wording check passes on the merged lines, and boldness — whose geometry
    # did not survive the rotation — abstains to review rather than measuring
    # the wrong pixels.
    passes: list[int] = []
    monkeypatch.setattr(service, "read_lines", _counting_real_read(passes))

    result = check_label(
        _rotated_fixture(degrees),
        fixture_corpus.application_for(SPEC),
        extractor=_PerfectExtractor(),
        recover_rotation=True,
    )

    findings = {f.rule_id: f for f in result.report.findings}
    assert findings["health_warning_verbatim"].verdict is Verdict.PASS
    # Boldness abstains, never verdicts, on a rotated warning. Which abstention
    # fires depends on which line the locator found: a retry-marked line takes
    # the rotated-frame branch (pinned in test_bold), while an upright pass that
    # read the sideways heading itself yields an unmeasurable sliver and takes
    # the too-small branch.
    assert findings["health_warning_bold"].verdict is Verdict.NEEDS_REVIEW
    # The retry fired and stayed inside its stated bound: the upright pass,
    # then rotations in a fixed order, stopping once the warning appeared.
    assert 2 <= len(passes) <= 4
    assert passes == [0, 90, 180, 270][: len(passes)]


def test_exif_oriented_input_reads_upright_without_a_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A phone-style upload: pixels stored sideways, the EXIF orientation tag
    # saying so. The bounded decode applies the tag, so OCR reads the label
    # upright on the first pass and — even with recovery armed — no rotation
    # pass is paid for.
    img = Image.open(io.BytesIO(FIXTURE.read_bytes())).convert("RGB")
    stored = img.transpose(Image.Transpose.ROTATE_90)  # undone by orientation 6
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    stored.save(buffer, format="JPEG", quality=95, exif=exif)

    passes: list[int] = []
    monkeypatch.setattr(service, "read_lines", _counting_real_read(passes))

    result = check_label(
        buffer.getvalue(),
        fixture_corpus.application_for(SPEC),
        extractor=_PerfectExtractor(),
        recover_rotation=True,
    )
    assert passes == [0]
    findings = {f.rule_id: f for f in result.report.findings}
    assert findings["health_warning_verbatim"].verdict is Verdict.PASS


def test_retry_does_not_fire_when_the_warning_reads_upright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passes: list[int] = []

    def stub(image: bytes, *, background: bool = False, rotation: int = 0):
        passes.append(rotation)
        return [OcrLine(text=REFERENCE, confidence=0.99)]

    monkeypatch.setattr(service, "read_lines", stub)
    check_label(_png(64, 64), Application(), extractor=_AbsentExtractor(), recover_rotation=True)
    assert passes == [0]  # one read; no rotation pass was paid for


def test_retry_stops_at_its_bound_and_merges_nothing_when_no_rotation_reads_the_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passes: list[int] = []

    def stub(image: bytes, *, background: bool = False, rotation: int = 0):
        passes.append(rotation)
        return [OcrLine(text="no warning here", confidence=0.9, rotation=rotation)]

    monkeypatch.setattr(service, "read_lines", stub)
    upright = [OcrLine(text="brand art only", confidence=0.9)]
    merged = _recover_rotated_warning(b"img", upright, background=False)
    assert passes == [90, 180, 270]  # the caller made the upright pass; three more is the bound
    assert merged is upright  # nothing appeared, so nothing was merged


def test_retry_merges_the_first_rotation_that_reads_the_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passes: list[int] = []

    def stub(image: bytes, *, background: bool = False, rotation: int = 0):
        passes.append(rotation)
        text = REFERENCE if rotation == 180 else "sideways art"
        return [OcrLine(text=text, confidence=0.9, rotation=rotation)]

    monkeypatch.setattr(service, "read_lines", stub)
    upright = [OcrLine(text="brand art only", confidence=0.9)]
    merged = _recover_rotated_warning(b"img", upright, background=False)
    assert passes == [90, 180]  # stopped at the first rotation where it appeared
    assert merged[: len(upright)] == upright  # upright lines keep first position
    extra = merged[len(upright) :]
    assert extra and all(line.rotation == 180 for line in extra)  # marked lines merged


def test_run_job_retries_a_miss_row_only_when_told_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # The checkbox's meaning at the runner: on, a row whose warning is never
    # found pays the three bounded retry passes; off, every row is a single
    # pass and nothing else — maximum throughput.
    for enabled, expected in [(True, [0, 90, 180, 270]), (False, [0])]:
        passes: list[int] = []

        def stub(image: bytes, *, background: bool = False, rotation: int = 0, _p=passes):
            _p.append(rotation)
            return [OcrLine(text="no warning here", confidence=0.9, rotation=rotation)]

        monkeypatch.setattr(service, "read_lines", stub)
        path = tmp_path / f"spool-{enabled}.png"
        path.write_bytes(_png(64, 64))
        job = create_job(["label.png"])
        asyncio.run(
            run_job(job, [("label.png", path)], _AbsentExtractor(), recover_rotation=enabled)
        )
        assert job.items[0].status == "done"
        assert passes == expected, f"recover_rotation={enabled} made passes {passes}"


def test_batch_route_parses_the_retry_checkbox(monkeypatch: pytest.MonkeyPatch) -> None:
    # A checked box is the only thing a browser sends; absence means unchecked.
    captured: list[bool] = []

    async def capture_run_job(
        job, files, extractor, budget=None, applications=None, recover_rotation=True
    ) -> None:
        captured.append(recover_rotation)
        batchmod.discard_spooled(path for _name, path in files)

    monkeypatch.setattr(batchmod, "run_job", capture_run_job)
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: _AbsentExtractor())
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    with TestClient(webapp.app) as c:
        for data in ({"recover_rotation": "on"}, None):
            resp = c.post(
                "/batch",
                files=[("images", ("a.png", png, "image/png"))],
                data=data,
                follow_redirects=False,
            )
            assert resp.status_code == 303
            job_id = resp.headers["location"].rsplit("/", 1)[-1]
            c.get(f"/batch/{job_id}/data")  # lets the scheduled job task run
    assert captured == [True, False]


# --- The interactive path: stated rotation, never a retry --------------------


def _preview_image_from(html: str) -> Image.Image:
    match = re.search(r'src="data:image/jpeg;base64,([^"]+)"', html)
    assert match, "no JPEG data URI found on the page"
    return Image.open(io.BytesIO(base64.b64decode(match.group(1))))


def test_interactive_route_straightens_a_sideways_upload_from_the_stated_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End to end through /check with real OCR: a scan that LOOKS rotated 90
    # degrees clockwise (its text reads top to bottom), submitted with the
    # select option a person would pick for it, is judged upright — the
    # wording check passes on the straightened raster — and the page echoes
    # the straightened image, which is the no-JS feedback loop: the reviewer
    # sees exactly what was judged.
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: _PerfectExtractor())
    sideways = _rotated_fixture(270)  # 270 counter-clockwise == looks 90 clockwise
    upright_size = Image.open(io.BytesIO(FIXTURE.read_bytes())).size
    assert Image.open(io.BytesIO(sideways)).size == upright_size[::-1]  # really sideways

    resp = client.post(
        "/check",
        files={"image": (SPEC.filename, sideways, "image/png")},
        data={
            "brand_name": SPEC.filed_brand,
            "class_type": SPEC.class_type,
            "rotation": "90",  # the user's answer to "how does it look?"
        },
    )
    assert resp.status_code == 200
    assert "Compliant" in resp.text  # every check passed on the straightened raster
    # The echoed preview is the corrected raster's orientation, not the upload's.
    preview = _preview_image_from(resp.text)
    assert (preview.width > preview.height) == (upright_size[0] > upright_size[1])


def test_interactive_route_never_pays_a_retry_pass_even_on_a_missing_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # recover_rotation defaults False through the route: a label whose warning
    # is never found still costs exactly one OCR pass, because the interactive
    # latency target is unconditional.
    passes: list[int] = []

    def stub(image: bytes, *, background: bool = False, rotation: int = 0):
        passes.append(rotation)
        return [OcrLine(text="no warning anywhere", confidence=0.9)]

    monkeypatch.setattr(service, "read_lines", stub)
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: _AbsentExtractor())
    resp = client.post(
        "/check",
        files={"image": ("l.png", _png(64, 64), "image/png")},
        data={"brand_name": "X", "class_type": "Y"},
    )
    assert resp.status_code == 200
    assert passes == [0]


@pytest.mark.parametrize("value", ["sideways", "45", "-90", "360"])
def test_route_rejects_a_garbage_rotation_value_cleanly(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hand-edited form value gets the same clean 4xx as other bad form input,
    # never a 500, and nothing is read or spent.
    calls: list[int] = []
    monkeypatch.setattr(webapp, "check_label", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: _AbsentExtractor())
    resp = client.post(
        "/check",
        files={"image": ("l.png", _png(64, 64), "image/png")},
        data={"brand_name": "X", "class_type": "Y", "rotation": value},
    )
    assert resp.status_code == 422
    assert "rotation choice" in resp.text
    assert calls == []


def test_check_label_rejects_an_unsupported_rotation() -> None:
    # The service contract mirrors read_lines: a rotation outside the map is a
    # programming error, raised before any decode or spend.
    with pytest.raises(ValueError, match="rotation"):
        check_label(_png(8, 8), Application(), extractor=_AbsentExtractor(), rotation=45)


def test_rotation_options_map_to_the_transpose_that_restores_the_pixels() -> None:
    # The mapping is judged from the user's side: the select value is how the
    # image LOOKS (degrees clockwise), and the fix is the same angle counter-
    # clockwise. Pinned at pixel level: upright reads RED then BLUE left to
    # right; a raster whose reading order runs top to bottom (RED above BLUE)
    # looks rotated 90 degrees clockwise, and must come back upright under the
    # "90 clockwise" option's value.
    upright = Image.new("RGB", (2, 1))
    upright.putpixel((0, 0), (255, 0, 0))
    upright.putpixel((1, 0), (0, 0, 255))

    def png_bytes(img: Image.Image) -> bytes:
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    looks_cw = upright.transpose(Image.Transpose.ROTATE_270)
    assert looks_cw.size == (1, 2)
    assert looks_cw.getpixel((0, 0)) == (255, 0, 0)  # reading order now runs downward

    for looks_like, option in [
        (looks_cw, 90),  # "90° clockwise"
        (upright.transpose(Image.Transpose.ROTATE_180), 180),  # "180° (upside down)"
        (upright.transpose(Image.Transpose.ROTATE_90), 270),  # "90° counter-clockwise"
    ]:
        restored = Image.open(io.BytesIO(transpose_image(png_bytes(looks_like), option)))
        assert restored.size == upright.size
        assert restored.tobytes() == upright.tobytes()


# --- The retry's lock contract ----------------------------------------------


class _ProbeLock:
    """Stands in for the OCR engine lock. Internally reentrant so a nested
    acquisition would surface as depth 2 in the assertions below instead of as
    the real Lock's silent deadlock."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.acquisitions = 0
        self.depth = 0
        self.max_depth = 0

    def acquire(self) -> bool:
        self._lock.acquire()
        self.acquisitions += 1
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        return True

    def release(self) -> None:
        self.depth -= 1
        self._lock.release()

    def __enter__(self) -> _ProbeLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def test_every_retry_pass_takes_the_engine_lock_and_never_nests_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The retry's lock contract, probed through the real read path: four
    # inferences, each inside its own acquisition, none while another is held —
    # so the priority gate's guarantee (an interactive check waits behind at
    # most one inference) survives the retry.
    probe = _ProbeLock()
    depths: list[int] = []
    shapes: list[tuple[int, ...]] = []

    def engine(array) -> tuple[None, float]:
        depths.append(probe.depth)
        shapes.append(tuple(array.shape[:2]))
        return None, 0.0

    monkeypatch.setattr(ocrmod, "_ENGINE_LOCK", probe)
    monkeypatch.setattr(ocrmod, "_engine", lambda: engine)
    check_label(_png(40, 20), Application(), extractor=_AbsentExtractor(), recover_rotation=True)

    assert probe.acquisitions == 4  # the upright pass and exactly three retries
    assert probe.max_depth == 1  # no pass ran inside another's acquisition
    assert depths == [1, 1, 1, 1]  # every inference ran under the lock
    # The raster the engine saw really was rotated inside the locked pass.
    assert shapes == [(20, 40), (40, 20), (20, 40), (40, 20)]
