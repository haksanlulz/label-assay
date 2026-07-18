"""Server-side failure records. Every broad except in the pipeline funnels
through a few chokepoints; each must leave a traceback in the log while the
user-facing surface stays a clean message — a production incident has no other
diagnosis path than these records."""

from __future__ import annotations

import asyncio
import io
import logging

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from label_assay.domain.models import Application, Verdict
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.fixture import FixtureExtractor
from label_assay.web import app as webapp
from label_assay.web import batch as batchmod
from label_assay.web.batch import create_job, run_job

client = TestClient(webapp.app)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _RaisingExtractor:
    def extract(self, image: bytes) -> Extraction:
        raise RuntimeError("simulated SDK failure")


def test_vision_failure_is_logged_and_the_page_stays_clean(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: _RaisingExtractor())
    with caplog.at_level(logging.ERROR, logger="label_assay.web.service"):
        resp = client.post(
            "/check",
            files={"image": ("l.png", _png(), "image/png")},
            data={"brand_name": "X", "class_type": "Y"},
        )
    assert resp.status_code == 503
    assert "The AI label reader was unavailable" in resp.text
    assert "simulated SDK failure" not in resp.text  # internals never reach the page
    record = next(r for r in caplog.records if "Vision extraction failed" in r.getMessage())
    assert record.exc_info is not None


def test_batch_item_pipeline_bug_is_logged_with_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path
) -> None:
    # A genuine code bug used to be reported identically to a bad file, forever,
    # invisibly; the log record is what tells them apart.
    def broken_check_label(*args, **kwargs):
        raise RuntimeError("pipeline bug")

    monkeypatch.setattr(batchmod, "check_label", broken_check_label)
    job = create_job(["a.png"])
    path = tmp_path / "a.png"
    path.write_bytes(_png())
    with caplog.at_level(logging.ERROR, logger="label_assay.web.batch"):
        asyncio.run(run_job(job, [("a.png", path)], FixtureExtractor({})))
    assert job.items[0].status == "error"
    assert job.items[0].detail == "Could not process this file."
    record = next(r for r in caplog.records if "unhandled error" in r.getMessage())
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError
    # The record is addressed by job id + item index; the uploaded filename is
    # user data and stays out of the server log.
    assert job.id in record.getMessage()
    assert all("a.png" not in r.getMessage() for r in caplog.records)


def test_batch_item_reader_failure_records_the_cause(
    caplog: pytest.LogCaptureFixture, tmp_path
) -> None:
    # FixtureExtractor({}) raises inside the reader; the row shows the clean
    # message while the log carries the chained cause — addressed by job id and
    # item index, never the uploaded filename (user data stays out of the log).
    job = create_job(["a.png"])
    path = tmp_path / "a.png"
    path.write_bytes(_png())
    with caplog.at_level(logging.WARNING, logger="label_assay.web.batch"):
        asyncio.run(run_job(job, [("a.png", path)], FixtureExtractor({})))
    assert job.items[0].status == "error"
    record = next(r for r in caplog.records if f"Batch job {job.id} item 0" in r.getMessage())
    assert record.exc_info is not None
    assert all("a.png" not in r.getMessage() for r in caplog.records)


def test_batch_task_crash_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    # A job-level crash used to be discarded by the done-callback; the item rows
    # then hang "pending" forever with no server-side trace.
    async def drive() -> None:
        async def boom() -> None:
            raise RuntimeError("job died")

        task = asyncio.get_running_loop().create_task(boom())
        webapp._BG_TASKS.add(task)
        task.add_done_callback(webapp._batch_task_done)
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)  # let the done-callback run

    with caplog.at_level(logging.ERROR, logger="label_assay.web.app"):
        asyncio.run(drive())
    record = next(r for r in caplog.records if "Batch job task crashed" in r.getMessage())
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError
    assert not webapp._BG_TASKS


def test_completed_check_logs_one_phase_timing_line_with_no_user_data(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Every completed check leaves exactly one INFO record attributing its time
    # to the vision call vs. the OCR read — that record is how a slow deployed
    # check is diagnosed — and it carries phase seconds only: no filename, no
    # label content.
    import re

    import fixture_corpus
    from label_assay.extract.fixture import fixture_key
    from label_assay.extract.ocr import OcrLine
    from label_assay.web import service as servicemod
    from label_assay.web.service import check_label

    spec = fixture_corpus.known_good_compliant()
    image = fixture_corpus.fixture_path(spec).read_bytes()
    fixture = FixtureExtractor({fixture_key(image): fixture_corpus.perfect_extraction(spec)})
    monkeypatch.setattr(
        servicemod,
        "read_lines",
        lambda _image, background=False, rotation=0: [
            OcrLine(fixture_corpus.mandated_warning(), 0.99)
        ],
    )
    with caplog.at_level(logging.INFO, logger="label_assay.web.service"):
        check_label(image, fixture_corpus.application_for(spec), extractor=fixture)

    timed = [r for r in caplog.records if "timed:" in r.getMessage()]
    assert len(timed) == 1
    message = timed[0].getMessage()
    assert re.fullmatch(r"check timed: vision=\d+\.\ds ocr=\d+\.\ds total=\d+\.\ds", message)
    assert spec.filename not in message
    assert ".png" not in message


def test_bold_check_failure_is_logged_and_degrades(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A cv2/numpy regression must degrade the 16.22 bold check to NOT_EVALUABLE
    # (never sink a verdict) — but not silently, or the dead check is invisible.
    import label_assay.match.bold as boldmod
    from label_assay.extract.ocr import OcrLine
    from label_assay.rulebook.loader import load_rulebook
    from label_assay.verify import engine as engmod

    def broken(image, ocr_lines):
        raise RuntimeError("cv2 regression")

    monkeypatch.setattr(boldmod, "check_warning_bold", broken)
    rule = next(r for r in load_rulebook().rules if r.match.strategy == "warning_bold")
    f = ExtractedField(verbatim=None, found=False, value=None)
    ctx = engmod.VerifyContext(
        extraction=Extraction(
            brand_name=f, class_type=f, alcohol_content=f, net_contents=f, government_warning=f
        ),
        application=Application(),
        ocr_lines=[OcrLine("GOVERNMENT WARNING", 0.9)],
        image=b"\x89PNG-not-really",
    )
    with caplog.at_level(logging.ERROR, logger="label_assay.verify.engine"):
        finding = engmod._match_warning_bold(rule, ctx)
    assert finding.verdict == Verdict.NOT_EVALUABLE
    record = next(r for r in caplog.records if "warning_bold" in r.getMessage())
    assert record.exc_info is not None
