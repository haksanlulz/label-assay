"""Batch processing: the concurrent job runner (offline) and the batch routes."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.fixture import FixtureExtractor
from label_assay.rulebook.loader import load_rulebook
from label_assay.web import app as webapp
from label_assay.web.batch import create_job, parse_application_csv, run_job

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "bourbon_compliant.png"
client = TestClient(webapp.app)


def _compliant_extraction() -> Extraction:
    warning = next(r for r in load_rulebook().rules if r.id == "health_warning_verbatim").match.reference

    def f(text: str) -> ExtractedField:
        return ExtractedField(verbatim=text, found=True, value=text)

    return Extraction(
        brand_name=f("OLD TOM DISTILLERY"),
        class_type=f("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=f("45% Alc./Vol. (90 Proof)"),
        net_contents=f("750 mL"),
        government_warning=f(warning),
    )


def test_batch_upload_form_renders() -> None:
    resp = client.get("/batch")
    assert resp.status_code == 200
    assert "many labels" in resp.text.lower()


@pytest.mark.skipif(not SAMPLE.exists(), reason="run samples/make_samples.py first")
def test_run_job_processes_every_item_offline() -> None:
    image = SAMPLE.read_bytes()
    fixture = FixtureExtractor({hashlib.sha256(image).hexdigest(): _compliant_extraction()})
    job = create_job(["a.png", "b.png"])
    asyncio.run(run_job(job, [("a.png", image), ("b.png", image)], fixture))

    assert job.done == 2
    assert all(item.status == "done" for item in job.items)
    counts = job.summary()
    assert counts["pass"] + counts["needs_review"] == 2  # a compliant label never fails


def test_batch_post_with_no_valid_images_errors() -> None:
    resp = client.post("/batch", files=[("images", ("x.txt", b"not an image", "text/plain"))])
    assert "No PNG or JPEG" in resp.text


def test_unknown_batch_is_404() -> None:
    assert client.get("/batch/deadbeef99").status_code == 404


def test_application_csv_is_parsed_and_headers_are_case_insensitive() -> None:
    raw = b"Filename,Brand_Name,Class_Type\na.png,Old Tom Distillery,Kentucky Straight Bourbon Whiskey\n"
    applications = parse_application_csv(raw)
    assert applications["a.png"].brand_name == "Old Tom Distillery"
    assert applications["a.png"].class_type == "Kentucky Straight Bourbon Whiskey"


def test_application_csv_skips_rows_without_a_filename() -> None:
    raw = b"filename,brand_name,class_type\n,Nobody,Whiskey\nb.png,Real Brand,Whiskey\n"
    applications = parse_application_csv(raw)
    assert list(applications) == ["b.png"]


def test_application_csv_tolerates_junk() -> None:
    assert parse_application_csv(b"") == {}
    assert parse_application_csv(b"nothing,useful\n1,2\n") == {}


@pytest.mark.skipif(not SAMPLE.exists(), reason="run samples/make_samples.py first")
def test_batch_checks_each_label_against_its_own_application() -> None:
    # The paired CSV is what makes brand-vs-application work in a batch: the same
    # image passes against its own filed brand and fails against someone else's.
    image = SAMPLE.read_bytes()
    fixture = FixtureExtractor({hashlib.sha256(image).hexdigest(): _compliant_extraction()})
    applications = parse_application_csv(
        b"filename,brand_name,class_type\n"
        b"right.png,Old Tom Distillery,Kentucky Straight Bourbon Whiskey\n"
        b"wrong.png,Buffalo Trace,Kentucky Straight Bourbon Whiskey\n"
    )
    job = create_job(["right.png", "wrong.png"])
    asyncio.run(run_job(job, [("right.png", image), ("wrong.png", image)], fixture, None, applications))

    by_name = {item.filename: item for item in job.items}
    assert by_name["right.png"].verdict == "pass"
    assert by_name["wrong.png"].verdict == "fail"


def test_csv_export() -> None:
    job = create_job(["x.png"])
    job.items[0].status = "done"
    job.items[0].verdict = "fail"
    job.items[0].detail = "Missing warning"
    resp = client.get(f"/batch/{job.id}/export.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "x.png" in resp.text and "fail" in resp.text
