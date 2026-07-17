"""FastAPI application — the imperative shell.

A single-label flow: upload a label image plus the application details, get a
verdict page. Server-rendered, no client JavaScript — the whole flow works with
scripting disabled. Infrastructure failures render a clean message, never a
stack trace.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from label_assay import __version__
from label_assay.config import get_settings
from label_assay.domain.models import Application, LabelReport, Verdict
from label_assay.extract.base import Extraction
from label_assay.rulebook.loader import load_rulebook
from label_assay.web import batch as batchmod
from label_assay.web.budget import DailyBudget
from label_assay.web.service import ExtractionUnavailable, check_label, default_extractor

logger = logging.getLogger(__name__)

_BG_TASKS: set = set()  # keep references so fire-and-forget batch jobs aren't GC'd
# Bounds what this public instance can spend in a day. The provider-side workspace
# spend cap is the hard ceiling; this makes the app degrade politely first.
_BUDGET = DailyBudget(limit_usd=get_settings().daily_budget_usd)

_WEB = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB / "templates"))

_MAX_BYTES = 5 * 1024 * 1024
_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")

# First-person for the uncertain state (research: it reduces over-reliance), and
# TTB's own "needs correction" vocabulary for a failure rather than a red "error".
_VERDICT_COPY = {
    Verdict.PASS: ("Compliant", "This label passed every automated check below."),
    Verdict.NEEDS_REVIEW: (
        "Needs your review",
        "I couldn't verify everything automatically. Please check the items marked for review below.",
    ),
    Verdict.FAIL: (
        "Needs correction",
        "This label has at least one problem that needs correction. See the findings below.",
    ),
}

# One owner for the plain-language per-finding vocabulary. The batch table's JS
# renders the same words from its own map (the two surfaces cannot share code
# across the wire); a test pins them equal.
_VERDICT_LABEL = {
    Verdict.PASS: "Compliant",
    Verdict.NEEDS_REVIEW: "Needs review",
    Verdict.FAIL: "Needs correction",
    Verdict.NOT_EVALUABLE: "Not checked",
}
_TEMPLATES.env.globals["verdict_label"] = _VERDICT_LABEL

# The reader's fields echoed back on the result page, in display order, using
# the form's own vocabulary. The government warning is rendered as presence
# only — its wording and format are already judged (and diffed) in the findings.
_READ_FIELDS = (
    ("brand_name", "Brand name"),
    ("class_type", "Class or type"),
    ("alcohol_content", "Alcohol content"),
    ("net_contents", "Net contents"),
)


_WARM_ON_STARTUP = True  # tests flip this off; a deployed process warms the reader


def _warm_reader() -> None:
    """One tiny generated image through the real extraction path, so the first
    user's check never pays the provider's connection + model cold start. Costs
    one budget reservation (~$0.005, EST_COST_PER_LABEL_USD) per process restart
    — accepted, because the first click after a deploy is the first impression.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        return  # nothing to warm and no paid call to make
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    try:
        check_label(
            buffer.getvalue(),
            Application(),
            extractor=default_extractor(settings),
            budget=_BUDGET,
            background=True,  # a user's first click still outranks the warm-up
        )
        logger.info("Reader warm-up complete")
    except ExtractionUnavailable as exc:
        # Budget exhausted or reader unavailable: the warm-up is an optimization,
        # so skip quietly rather than mark the boot degraded.
        logger.info("Reader warm-up skipped: %s", exc)
    except Exception:
        logger.warning("Reader warm-up failed; first request pays the cold start", exc_info=True)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Boot-time validation and warmup: a corrupt rulebook should fail the deploy
    # right here, not 500 on the first request, and the OCR engine's multi-second
    # init should be paid before traffic, not inside the first user's check.
    logging.basicConfig(level=logging.INFO)
    load_rulebook()
    await asyncio.to_thread(_ocr_status)
    if _WARM_ON_STARTUP and get_settings().anthropic_api_key:
        # Fire-and-forget: startup never blocks on (or crashes from) a network
        # call — _warm_reader catches everything and only logs.
        task = asyncio.create_task(asyncio.to_thread(_warm_reader))
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
    yield


app = FastAPI(title="LabelAssay", version=__version__, lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(_WEB / "static")), name="static")


def _ctx(extra: dict) -> dict:
    return {"version": __version__, **extra}


def _report_page(
    request: Request, report: LabelReport, extraction: Extraction, elapsed: float | None = None
) -> HTMLResponse:
    heading, summary = _VERDICT_COPY.get(report.verdict, ("Result", ""))
    return _TEMPLATES.TemplateResponse(
        request,
        "result.html",
        _ctx(
            {
                "report": report,
                "heading": heading,
                "summary": summary,
                "elapsed": elapsed,
                "extraction": extraction,
                "read_fields": [(label, getattr(extraction, name)) for name, label in _READ_FIELDS],
            }
        ),
    )


def _error_page(request: Request, message: str, status: int = 200) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(
        request, "error.html", _ctx({"message": message}), status_code=status
    )


_OCR_READY: str | None = None


def _ocr_status() -> str:
    """Prove the local OCR engine actually loads on this host. Success is cached
    (the cost is paid once per process); a failure is re-probed on the next call,
    so one transient bad probe cannot report the engine dead for the life of the
    process. The vision wheels install cleanly and only fail at import, so 'it
    deployed' is not evidence that this works."""
    global _OCR_READY
    if _OCR_READY is not None:
        return _OCR_READY
    try:
        from PIL import Image

        from label_assay.extract.ocr import read_lines

        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
        read_lines(buffer.getvalue())  # a blank image finds no text; loading is the point
        _OCR_READY = "ready"
        return _OCR_READY
    except Exception as exc:
        logger.warning("OCR readiness probe failed", exc_info=exc)
        return f"failed: {type(exc).__name__}: {exc}"[:160]


@app.get("/health")
def health() -> dict[str, object]:
    """Readiness of each subsystem, so an uptime check or a reviewer sees a
    degraded state rather than guessing from a generic error page."""
    rulebook = load_rulebook()
    settings = get_settings()
    ocr = _ocr_status()
    return {
        # The top-level status must not contradict the subsystem fields below.
        "status": "ok" if ocr == "ready" else "degraded",
        "version": __version__,
        "rulebook_version": rulebook.version,
        "rulebook_rules": len(rulebook.rules),
        "ai_reader": "configured" if settings.anthropic_api_key else "not-configured",
        "ocr": ocr,
        # Which instance answered. Batch job state lives in this process, so the
        # app must run as a single instance; seeing two ids here means it does not.
        "instance": os.environ.get("FLY_MACHINE_ID", "local"),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(request, "index.html", _ctx({}))


@app.post("/check", response_class=HTMLResponse)
async def check(
    request: Request,
    image: UploadFile,
    brand_name: str = Form(...),
    class_type: str = Form(...),
    fanciful_name: str = Form(""),
) -> HTMLResponse:
    # The multipart parser reports the spooled size; checking it before .read()
    # keeps an oversized upload from being materialized in memory first. The
    # post-read check stays as the fallback when no size is reported.
    if (image.size or 0) > _MAX_BYTES:
        return _error_page(request, "That image is larger than 5 MB. Please use a smaller file.", 413)
    data = await image.read()
    if len(data) > _MAX_BYTES:
        return _error_page(request, "That image is larger than 5 MB. Please use a smaller file.", 413)
    if not data.startswith(_MAGIC):
        return _error_page(request, "That file doesn't look like a PNG or JPEG image.", 415)

    application = Application(
        brand_name=brand_name.strip(),
        class_type=class_type.strip(),
        fanciful_name=fanciful_name.strip(),
    )
    started = time.perf_counter()
    try:
        # Off the event loop, exactly as the batch path runs it: check_label is
        # CPU-bound OCR plus a synchronous network call, and running it inline
        # would freeze every other request for its duration.
        result = await asyncio.to_thread(
            check_label, data, application, extractor=default_extractor(get_settings()), budget=_BUDGET
        )
    except ExtractionUnavailable as exc:
        # 503 so a monitor or scripted client can tell this failure from a
        # rendered verdict; the page itself is the same clean message either way.
        return _error_page(request, str(exc), 503)
    return _report_page(
        request, result.report, result.extraction, elapsed=time.perf_counter() - started
    )


@app.get("/batch", response_class=HTMLResponse)
def batch_new(request: Request) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(
        request,
        "batch_upload.html",
        _ctx(
            {
                "max_files": batchmod.MAX_FILES,
                "max_mb": batchmod.MAX_TOTAL_DISK_BYTES // (1024 * 1024),
            }
        ),
    )


_TOO_LARGE_DETAIL = "This file is larger than 5 MB, so it was not checked. Please use a smaller scan."
_CSV_TOO_LARGE = (
    "That applications file is larger than 5 MB. A batch of a few hundred "
    "applications is far smaller; please check the file."
)

_SPOOL_CHUNK = 1024 * 1024


async def _spool_upload(upload: UploadFile) -> tuple[Path, int] | str:
    """Stream one batch upload to a named temp file, enforcing the 5 MB cap and
    the magic-byte check during the copy — the file is judged as it streams, so
    an oversized or non-image upload never lands whole anywhere. Returns the
    temp path and byte count, or the user-facing reason the file was rejected
    (with nothing left on disk)."""
    # Fast path: when the multipart parser reports a size, an oversized file is
    # refused before a single byte is copied.
    if (upload.size or 0) > _MAX_BYTES:
        return _TOO_LARGE_DETAIL
    first = await upload.read(_SPOOL_CHUNK)
    if not first:
        return "This file is empty, so it was not checked."
    if not first.startswith(_MAGIC):
        return "Not a PNG or JPEG image, so this file was not checked."
    handle = tempfile.NamedTemporaryFile(delete=False, prefix="label-assay-", suffix=".upload")
    path = Path(handle.name)
    copied = 0
    spooled = False
    try:
        with handle:
            chunk = first
            while chunk:
                copied += len(chunk)
                if copied > _MAX_BYTES:
                    return _TOO_LARGE_DETAIL
                handle.write(chunk)
                chunk = await upload.read(_SPOOL_CHUNK)
        spooled = True
        return path, copied
    finally:
        if not spooled:  # rejected mid-copy, or the read/write itself failed
            path.unlink(missing_ok=True)


def _batch_task_done(task: asyncio.Task) -> None:
    _BG_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # A job-level crash leaves every pending item stuck; without this record
        # the traceback surfaces only as a GC-time warning, if at all.
        logger.error("Batch job task crashed", exc_info=exc)


@app.post("/batch")
async def batch_create(
    request: Request,
    images: list[UploadFile],
    applications: UploadFile | None = None,
):
    files: list[tuple[str, Path]] = []  # (filename, spooled temp path)
    rejected: list[tuple[str, str]] = []  # (filename, why the file was not checked)

    def reject(message: str, status: int) -> HTMLResponse:
        # Every early exit owns the spooled files it strands.
        batchmod.discard_spooled(path for _name, path in files)
        return _error_page(request, message, status)

    try:
        total = 0
        for upload in images:
            name = upload.filename or "label"
            # Each file streams to its own temp file with the 5 MB cap and the
            # magic-byte check applied during the copy, so a batch is never
            # materialized in memory — a worker later reads one file at a time.
            # The 5 MB cap also sits safely under the vision API's per-image
            # limit (~7.5 MB raw), so the extractor is never handed a payload
            # the API rejects for size.
            spooled = await _spool_upload(upload)
            if isinstance(spooled, str):
                rejected.append((name, spooled))
                continue
            path, size = spooled
            files.append((name, path))
            total += size
            if total > batchmod.MAX_TOTAL_DISK_BYTES:
                return reject(
                    "That batch is too large to process in one go. Please split it into "
                    "smaller batches.",
                    413,
                )

        if not files:
            return reject("No PNG or JPEG images were uploaded.", 400)
        if len(files) > batchmod.MAX_FILES:
            return reject(
                f"A batch is limited to {batchmod.MAX_FILES} labels. Please split it up.", 413
            )

        application_map: dict[str, Application] = {}
        csv_rows: int | None = None
        if applications is not None:
            if (applications.size or 0) > batchmod.MAX_CSV_BYTES:
                return reject(_CSV_TOO_LARGE, 413)
            raw = await applications.read()
            if len(raw) > batchmod.MAX_CSV_BYTES:
                return reject(_CSV_TOO_LARGE, 413)
            if raw:
                try:
                    application_map = batchmod.parse_application_csv(raw)
                except batchmod.ApplicationCSVError as exc:
                    return reject(str(exc), 415)
                csv_rows = len(application_map)

        uploaded_keys = {batchmod.pairing_key(name) for name, _ in files}
        if application_map and not uploaded_keys & set(application_map):
            # A CSV that matches nothing is a mistake (wrong column, wrong export),
            # not a smaller batch; running anyway would abstain on every brand check
            # with no hint why.
            return reject(
                "The applications CSV did not match any uploaded file name. The "
                "filename column must match the uploaded image names.",
                400,
            )

        try:
            extractor = default_extractor(get_settings())
        except ExtractionUnavailable as exc:
            return reject(str(exc), 503)
    except BaseException:
        # An unexpected failure anywhere above must not strand spooled files;
        # past this point run_job owns them and deletes as it goes.
        batchmod.discard_spooled(path for _name, path in files)
        raise

    job = batchmod.create_job([name for name, _ in files] + [name for name, _ in rejected])
    # Rejected files ride along as pre-completed error rows, so the results
    # table, the summary counts, and the CSV export account for every file the
    # user selected. They sit after the checkable files: run_job addresses
    # job.items positionally over `files`, so the leading indices must line up.
    for item, (_name, why) in zip(job.items[len(files) :], rejected):
        item.status, item.detail = "error", why
    if csv_rows is not None:
        job.csv_rows = csv_rows
        job.csv_unmatched = sum(1 for key in uploaded_keys if key not in application_map)
    task = asyncio.create_task(batchmod.run_job(job, files, extractor, _BUDGET, application_map))
    _BG_TASKS.add(task)
    task.add_done_callback(_batch_task_done)
    return RedirectResponse(f"/batch/{job.id}", status_code=303)


@app.get("/batch/{job_id}", response_class=HTMLResponse)
def batch_result(request: Request, job_id: str) -> HTMLResponse:
    if batchmod.get_job(job_id) is None:
        return _error_page(request, "That batch was not found. It may have expired.", 404)
    return _TEMPLATES.TemplateResponse(request, "batch_result.html", _ctx({"job_id": job_id}))


@app.get("/batch/{job_id}/data")
def batch_data(job_id: str) -> JSONResponse:
    job = batchmod.get_job(job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(
        {
            "total": job.total,
            "done": job.done,
            "summary": job.summary(),
            # null when no CSV was uploaded; counts when one was, so the page
            # can say how much of it actually paired with the uploaded files.
            "csv_rows": job.csv_rows,
            "csv_unmatched": job.csv_unmatched,
            "items": [
                {"filename": i.filename, "status": i.status, "verdict": i.verdict, "detail": i.detail}
                for i in job.items
            ],
        }
    )


@app.get("/batch/{job_id}/export.csv")
def batch_csv(job_id: str) -> Response:
    job = batchmod.get_job(job_id)
    if job is None:
        return Response("batch not found", status_code=404, media_type="text/plain")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["filename", "status", "verdict", "detail"])
    for item in job.items:
        writer.writerow([item.filename, item.status, item.verdict or "", item.detail or ""])
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="label-assay-{job_id}.csv"'},
    )
