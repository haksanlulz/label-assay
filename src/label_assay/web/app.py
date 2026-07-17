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
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from label_assay import __version__
from label_assay.config import get_settings
from label_assay.domain.models import Application, Verdict
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


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Boot-time validation and warmup: a corrupt rulebook should fail the deploy
    # right here, not 500 on the first request, and the OCR engine's multi-second
    # init should be paid before traffic, not inside the first user's check.
    logging.basicConfig(level=logging.INFO)
    load_rulebook()
    await asyncio.to_thread(_ocr_status)
    yield


app = FastAPI(title="LabelAssay", version=__version__, lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(_WEB / "static")), name="static")


def _ctx(extra: dict) -> dict:
    return {"version": __version__, **extra}


def _report_page(request: Request, report, elapsed: float | None = None) -> HTMLResponse:
    heading, summary = _VERDICT_COPY.get(report.verdict, ("Result", ""))
    return _TEMPLATES.TemplateResponse(
        request,
        "result.html",
        _ctx({"report": report, "heading": heading, "summary": summary, "elapsed": elapsed}),
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
        report = await asyncio.to_thread(
            check_label, data, application, extractor=default_extractor(get_settings()), budget=_BUDGET
        )
    except ExtractionUnavailable as exc:
        # 503 so a monitor or scripted client can tell this failure from a
        # rendered verdict; the page itself is the same clean message either way.
        return _error_page(request, str(exc), 503)
    return _report_page(request, report, elapsed=time.perf_counter() - started)


@app.get("/batch", response_class=HTMLResponse)
def batch_new(request: Request) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(
        request,
        "batch_upload.html",
        _ctx({"max_files": batchmod.MAX_FILES, "max_mb": batchmod.MAX_TOTAL_BYTES // (1024 * 1024)}),
    )


_TOO_LARGE_DETAIL = "This file is larger than 5 MB, so it was not checked. Please use a smaller scan."
_CSV_TOO_LARGE = (
    "That applications file is larger than 5 MB. A batch of a few hundred "
    "applications is far smaller; please check the file."
)


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
    files: list[tuple[str, bytes]] = []
    rejected: list[tuple[str, str]] = []  # (filename, why the file was not checked)
    total = 0
    for upload in images:
        name = upload.filename or "label"
        # Same pre-read size check as the single path, so one huge file cannot
        # spike memory before it is even inspected. The 5 MB cap also sits
        # safely under the vision API's per-image limit (~7.5 MB raw), so the
        # extractor is never handed a payload the API rejects for size.
        if (upload.size or 0) > _MAX_BYTES:
            rejected.append((name, _TOO_LARGE_DETAIL))
            continue
        data = await upload.read()
        if not data:
            rejected.append((name, "This file is empty, so it was not checked."))
            continue
        if not data.startswith(_MAGIC):
            rejected.append((name, "Not a PNG or JPEG image, so this file was not checked."))
            continue
        if len(data) > _MAX_BYTES:
            rejected.append((name, _TOO_LARGE_DETAIL))
            continue
        total += len(data)
        if total > batchmod.MAX_TOTAL_BYTES:
            return _error_page(
                request,
                "That batch is too large to process in one go. Please split it into "
                "smaller batches.",
                413,
            )
        files.append((name, data))

    if not files:
        return _error_page(request, "No PNG or JPEG images were uploaded.", 400)
    if len(files) > batchmod.MAX_FILES:
        return _error_page(
            request, f"A batch is limited to {batchmod.MAX_FILES} labels. Please split it up.", 413
        )

    application_map: dict[str, Application] = {}
    csv_rows: int | None = None
    if applications is not None:
        if (applications.size or 0) > batchmod.MAX_CSV_BYTES:
            return _error_page(request, _CSV_TOO_LARGE, 413)
        raw = await applications.read()
        if len(raw) > batchmod.MAX_CSV_BYTES:
            return _error_page(request, _CSV_TOO_LARGE, 413)
        if raw:
            try:
                application_map = batchmod.parse_application_csv(raw)
            except batchmod.ApplicationCSVError as exc:
                return _error_page(request, str(exc), 415)
            csv_rows = len(application_map)

    uploaded_keys = {batchmod.pairing_key(name) for name, _ in files}
    if application_map and not uploaded_keys & set(application_map):
        # A CSV that matches nothing is a mistake (wrong column, wrong export),
        # not a smaller batch; running anyway would abstain on every brand check
        # with no hint why.
        return _error_page(
            request,
            "The applications CSV did not match any uploaded file name. The "
            "filename column must match the uploaded image names.",
            400,
        )

    try:
        extractor = default_extractor(get_settings())
    except ExtractionUnavailable as exc:
        return _error_page(request, str(exc), 503)

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
