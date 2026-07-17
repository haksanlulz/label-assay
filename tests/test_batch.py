"""Batch processing: the concurrent job runner (offline) and the batch routes."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

import fixture_corpus
from label_assay.extract.fixture import FixtureExtractor
from label_assay.match.brand import BrandVerdict, match_brand
from label_assay.web import app as webapp
from label_assay.web.batch import create_job, parse_application_csv, run_job

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)
client = TestClient(webapp.app)


def test_batch_upload_form_renders() -> None:
    resp = client.get("/batch")
    assert resp.status_code == 200
    assert "many labels" in resp.text.lower()


@pytest.mark.skipif(not FIXTURE.exists(), reason="run tools/make_test_labels.py first")
def test_run_job_processes_every_item_offline() -> None:
    image = FIXTURE.read_bytes()
    fixture = FixtureExtractor(
        {hashlib.sha256(image).hexdigest(): fixture_corpus.perfect_extraction(SPEC)}
    )
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


@pytest.mark.skipif(not FIXTURE.exists(), reason="run tools/make_test_labels.py first")
def test_batch_checks_each_label_against_its_own_application() -> None:
    # The paired CSV is what makes brand-vs-application work in a batch: the same
    # image passes against its own filed brand and fails against someone else's.
    image = FIXTURE.read_bytes()
    fixture = FixtureExtractor(
        {hashlib.sha256(image).hexdigest(): fixture_corpus.perfect_extraction(SPEC)}
    )
    other_brand = next(  # an invented brand the matcher must call a real mismatch
        b
        for b in fixture_corpus.generator().BRANDS
        if match_brand(SPEC.painted_brand, b).verdict == BrandVerdict.MISMATCH
    )
    applications = parse_application_csv(
        (
            f"filename,brand_name,class_type\n"
            f"right.png,{SPEC.filed_brand},{SPEC.class_type}\n"
            f"wrong.png,{other_brand},{SPEC.class_type}\n"
        ).encode("utf-8")
    )
    job = create_job(["right.png", "wrong.png"])
    asyncio.run(
        run_job(job, [("right.png", image), ("wrong.png", image)], fixture, None, applications)
    )

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
