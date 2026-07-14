"""FastAPI application — the imperative shell.

A single-label flow: upload a label image plus the application details, get a
verdict page. Server-rendered, no client JavaScript — the whole flow works with
scripting disabled. Infrastructure failures render a clean message, never a
stack trace.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from label_assay import __version__
from label_assay.config import get_settings
from label_assay.domain.models import Application, Verdict
from label_assay.rulebook.loader import load_rulebook
from label_assay.web.service import ExtractionUnavailable, check_label, default_extractor

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

app = FastAPI(title="LabelAssay", version=__version__)
app.mount("/static", StaticFiles(directory=str(_WEB / "static")), name="static")


def _find_sample() -> Path | None:
    for base in (Path.cwd(), _WEB.parents[2]):
        candidate = base / "samples" / "bourbon_compliant.png"
        if candidate.exists():
            return candidate
    return None


def _ctx(extra: dict) -> dict:
    return {"version": __version__, **extra}


def _report_page(request: Request, report) -> HTMLResponse:
    heading, summary = _VERDICT_COPY.get(report.verdict, ("Result", ""))
    return _TEMPLATES.TemplateResponse(
        request, "result.html", _ctx({"report": report, "heading": heading, "summary": summary})
    )


def _error_page(request: Request, message: str, status: int = 200) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(
        request, "error.html", _ctx({"message": message}), status_code=status
    )


@app.get("/health")
def health() -> dict[str, object]:
    rulebook = load_rulebook()
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "rulebook_version": rulebook.version,
        "rulebook_rules": len(rulebook.rules),
        "extractor": "ready" if settings.anthropic_api_key else "not-configured",
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
) -> HTMLResponse:
    data = await image.read()
    if len(data) > _MAX_BYTES:
        return _error_page(request, "That image is larger than 5 MB. Please use a smaller file.", 413)
    if not data.startswith(_MAGIC):
        return _error_page(request, "That file doesn't look like a PNG or JPEG image.", 415)

    application = Application(brand_name=brand_name.strip(), class_type=class_type.strip())
    try:
        report = check_label(data, application, extractor=default_extractor(get_settings()))
    except ExtractionUnavailable as exc:
        return _error_page(request, str(exc))
    return _report_page(request, report)


@app.get("/sample", response_class=HTMLResponse)
def sample(request: Request) -> HTMLResponse:
    path = _find_sample()
    if path is None:
        return _error_page(request, "The sample label isn't available on this server.")
    application = Application(brand_name="Old Tom Distillery", class_type="Kentucky Straight Bourbon Whiskey")
    try:
        report = check_label(path.read_bytes(), application, extractor=default_extractor(get_settings()))
    except ExtractionUnavailable as exc:
        return _error_page(request, str(exc))
    return _report_page(request, report)
