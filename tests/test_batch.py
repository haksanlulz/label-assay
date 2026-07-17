"""Batch processing: the concurrent job runner (offline) and the batch routes."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import time

import pytest
from fastapi.testclient import TestClient

import fixture_corpus
from label_assay.extract.fixture import FixtureExtractor
from label_assay.match.brand import BrandVerdict, match_brand
from label_assay.web import app as webapp
from label_assay.web import batch as batchmod
from label_assay.web.batch import ApplicationCSVError, create_job, parse_application_csv, run_job
from synthetic_images import bomb_png

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)
client = TestClient(webapp.app)


def test_batch_upload_form_renders() -> None:
    resp = client.get("/batch")
    assert resp.status_code == 200
    assert "many labels" in resp.text.lower()


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
    assert resp.status_code == 400
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


def test_application_csv_with_no_content_is_empty() -> None:
    assert parse_application_csv(b"") == {}


def test_application_csv_without_a_filename_column_raises() -> None:
    # Silently accepting a wrong-headers CSV would abstain on every brand
    # comparison with no hint the file was ignored.
    with pytest.raises(ApplicationCSVError, match="filename"):
        parse_application_csv(b"nothing,useful\n1,2\n")


def test_application_csv_pairing_is_case_and_path_insensitive() -> None:
    raw = b"filename,brand_name,class_type\nlabels/Label1.PNG,Old Tom Distillery,Whiskey\n"
    applications = parse_application_csv(raw)
    assert list(applications) == ["label1.png"]
    assert batchmod.pairing_key("LABEL1.png") in applications
    assert batchmod.pairing_key("scans\\Label1.PNG") in applications


def test_application_csv_rejects_binary_content_with_a_typed_error() -> None:
    # A PNG or spreadsheet picked into the CSV field must raise the typed error
    # (rendered as a clean page), not leak csv.Error into a 500.
    with pytest.raises(ApplicationCSVError):
        parse_application_csv(bomb_png(64, 64))
    with pytest.raises(ApplicationCSVError):
        parse_application_csv(b"a" * 200_000)  # a field over csv's 131072 limit


def test_batch_with_a_binary_applications_file_gets_a_clean_message() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    resp = client.post(
        "/batch",
        files=[
            ("images", ("a.png", png, "image/png")),
            ("applications", ("apps.csv", bomb_png(64, 64), "text/csv")),
        ],
    )
    assert resp.status_code == 415
    assert "could not be read as a CSV" in resp.text


def test_batch_over_the_total_size_cap_is_asked_to_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batchmod, "MAX_TOTAL_BYTES", 10_000)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8_000
    resp = client.post(
        "/batch",
        files=[("images", (f"l{i}.png", png, "image/png")) for i in range(2)],
    )
    assert resp.status_code == 413
    assert "split it into" in resp.text


def test_batch_upload_page_states_the_size_cap() -> None:
    resp = client.get("/batch")
    cap_mb = batchmod.MAX_TOTAL_BYTES // (1024 * 1024)
    assert f"{cap_mb}" in resp.text and "MB" in resp.text


def test_oversized_image_in_a_batch_becomes_an_item_error() -> None:
    # One decompression bomb must degrade to a per-item error, never sink the
    # batch or the process.
    job = create_job(["bomb.png"])
    asyncio.run(run_job(job, [("bomb.png", bomb_png(8000, 6000))], FixtureExtractor({})))
    assert job.items[0].status == "error"
    assert "too large" in (job.items[0].detail or "")


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
    buf = io.StringIO()
    writer = csv.writer(buf)  # quotes as needed, so a brand containing a comma survives
    writer.writerow(["filename", "brand_name", "class_type"])
    writer.writerow(["right.png", SPEC.filed_brand, SPEC.class_type])
    writer.writerow(["wrong.png", other_brand, SPEC.class_type])
    applications = parse_application_csv(buf.getvalue().encode("utf-8"))
    job = create_job(["right.png", "wrong.png"])
    asyncio.run(
        run_job(job, [("right.png", image), ("wrong.png", image)], fixture, None, applications)
    )

    by_name = {item.filename: item for item in job.items}
    assert by_name["right.png"].verdict == "pass"
    assert by_name["wrong.png"].verdict == "fail"


def _tiny_png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


async def _noop_run_job(job, files, extractor, budget=None, applications=None) -> None:
    return None


def test_batch_route_happy_path_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole batch web slice through real routes: multipart parse, the 303,
    # the result page, the background task, and the /data JSON contract that
    # batch.js consumes. The CSV filename is deliberately cased differently from
    # the upload to pin case-insensitive pairing end to end.
    image = FIXTURE.read_bytes()
    fixture = FixtureExtractor(
        {hashlib.sha256(image).hexdigest(): fixture_corpus.perfect_extraction(SPEC)}
    )
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: fixture)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["filename", "brand_name", "class_type"])
    writer.writerow(["A.PNG", SPEC.filed_brand, SPEC.class_type])

    # The context-managed client keeps one event loop alive across requests, so
    # the create_task background job actually runs between polls.
    with TestClient(webapp.app) as c:
        resp = c.post(
            "/batch",
            files=[
                ("images", ("a.png", image, "image/png")),
                ("images", ("B.PNG", image, "image/png")),
                ("applications", ("apps.csv", buf.getvalue().encode(), "text/csv")),
            ],
            follow_redirects=False,
        )
        assert resp.status_code == 303
        location = resp.headers["location"]
        job_id = location.rsplit("/", 1)[-1]

        page = c.get(location)
        assert page.status_code == 200
        assert job_id in page.text  # batch_result.html carries the id for the poller

        deadline = time.time() + 60
        while True:
            data = c.get(f"/batch/{job_id}/data").json()
            if data["done"] == data["total"]:
                break
            assert time.time() < deadline, "batch never finished"
            time.sleep(0.1)

    assert {"total", "done", "summary", "items", "csv_rows", "csv_unmatched"} <= set(data)
    assert data["total"] == 2
    assert data["csv_rows"] == 1
    assert data["csv_unmatched"] == 1  # B.PNG has no application row
    by_name = {i["filename"]: i for i in data["items"]}
    assert set(by_name["a.png"]) == {"filename", "status", "verdict", "detail"}
    assert by_name["a.png"]["status"] == "done"
    assert by_name["a.png"]["verdict"] == "pass"  # paired via A.PNG despite the case
    assert by_name["a.png"]["detail"] == "All automated checks passed."
    assert by_name["B.PNG"]["status"] == "done"
    assert by_name["B.PNG"]["verdict"] in ("pass", "needs_review")
    if by_name["B.PNG"]["verdict"] == "pass":
        # No application row: the headline must not claim every check ran.
        assert by_name["B.PNG"]["detail"].startswith("All checks that could run passed")


def test_batch_keeps_rejected_files_visible_as_error_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Non-image and empty files used to vanish from the job with no trace; in a
    # compliance workflow those labels silently went unverified.
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: FixtureExtractor({}))
    monkeypatch.setattr(batchmod, "run_job", _noop_run_job)
    with TestClient(webapp.app) as c:
        resp = c.post(
            "/batch",
            files=[
                ("images", ("good.png", _tiny_png(), "image/png")),
                ("images", ("notes.txt", b"not an image", "text/plain")),
                ("images", ("empty.png", b"", "image/png")),
            ],
            follow_redirects=False,
        )
        assert resp.status_code == 303
        job_id = resp.headers["location"].rsplit("/", 1)[-1]
        data = c.get(f"/batch/{job_id}/data").json()
        assert data["total"] == 3
        by_name = {i["filename"]: i for i in data["items"]}
        assert by_name["notes.txt"]["status"] == "error"
        assert "not checked" in by_name["notes.txt"]["detail"]
        assert by_name["empty.png"]["status"] == "error"
        assert "empty" in by_name["empty.png"]["detail"]
        assert data["summary"]["error"] == 2
        export = c.get(f"/batch/{job_id}/export.csv").text
        assert "notes.txt" in export and "empty.png" in export


def test_oversized_batch_file_becomes_an_error_row_not_a_silent_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The 5 MB per-file cap applies on the batch path too, and the rejection is
    # a visible per-item error, not a whole-batch failure or a silent skip.
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: FixtureExtractor({}))
    monkeypatch.setattr(batchmod, "run_job", _noop_run_job)
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024)
    with TestClient(webapp.app) as c:
        resp = c.post(
            "/batch",
            files=[
                ("images", ("big.png", big, "image/png")),
                ("images", ("small.png", _tiny_png(), "image/png")),
            ],
            follow_redirects=False,
        )
        assert resp.status_code == 303
        job_id = resp.headers["location"].rsplit("/", 1)[-1]
        data = c.get(f"/batch/{job_id}/data").json()
        by_name = {i["filename"]: i for i in data["items"]}
        assert by_name["big.png"]["status"] == "error"
        assert "larger than 5 MB" in by_name["big.png"]["detail"]
        assert by_name["small.png"]["status"] == "pending"  # the stubbed runner never ran


def test_batch_over_the_file_count_cap_is_asked_to_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(batchmod, "MAX_FILES", 2)
    resp = client.post(
        "/batch",
        files=[("images", (f"l{i}.png", _tiny_png(), "image/png")) for i in range(3)],
    )
    assert resp.status_code == 413
    assert "limited to 2 labels" in resp.text


def test_oversized_applications_csv_is_rejected_cleanly() -> None:
    big_csv = b"filename,brand_name,class_type\n" + b"a" * (batchmod.MAX_CSV_BYTES + 1)
    resp = client.post(
        "/batch",
        files=[
            ("images", ("a.png", _tiny_png(), "image/png")),
            ("applications", ("apps.csv", big_csv, "text/csv")),
        ],
    )
    assert resp.status_code == 413
    assert "applications file is larger than 5 MB" in resp.text


def test_csv_matching_no_uploaded_file_is_rejected_with_a_clear_message() -> None:
    # The wrong-export failure mode: a CSV that parses but pairs with nothing
    # must not run a batch where every brand comparison silently abstains.
    csv_bytes = b"filename,brand_name,class_type\nother.png,Brand,Whiskey\n"
    resp = client.post(
        "/batch",
        files=[
            ("images", ("a.png", _tiny_png(), "image/png")),
            ("applications", ("apps.csv", csv_bytes, "text/csv")),
        ],
    )
    assert resp.status_code == 400
    assert "did not match any uploaded file name" in resp.text


def test_headline_does_not_claim_all_checks_passed_over_an_abstention() -> None:
    from label_assay.domain.models import Finding, LabelReport, Verdict

    report = LabelReport(
        verdict=Verdict.PASS,
        findings=[
            Finding(rule_id="w", citation="27 CFR 16.21", verdict=Verdict.PASS, detail="ok"),
            Finding(
                rule_id="b",
                citation="27 CFR 5.64",
                verdict=Verdict.NOT_EVALUABLE,
                detail="No application brand name was provided to compare against.",
            ),
        ],
        rulebook_version="x",
    )
    headline = batchmod._headline(report)
    assert headline != "All automated checks passed."
    assert "could run passed" in headline
    assert "No application brand name" in headline


def test_csv_export() -> None:
    job = create_job(["x.png"])
    job.items[0].status = "done"
    job.items[0].verdict = "fail"
    job.items[0].detail = "Missing warning"
    resp = client.get(f"/batch/{job.id}/export.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "x.png" in resp.text and "fail" in resp.text
