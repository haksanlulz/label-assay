"""FastAPI application — the imperative shell.

Day-1 skeleton: a landing page and a health check that reports subsystem
readiness. The upload, verification, and batch routes wire in at later stages.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ttb_verifier import __version__
from ttb_verifier.rulebook.loader import load_rulebook

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="TTB Label Verifier", version=__version__)


@app.get("/health")
def health() -> dict[str, object]:
    """Readiness of each subsystem, so an uptime check or a reviewer sees a
    degraded state rather than a blank 500. The extractor reports 'not-wired'
    until the Day-3 stage lands."""
    rulebook = load_rulebook()
    return {
        "status": "ok",
        "version": __version__,
        "rulebook_version": rulebook.version,
        "rulebook_rules": len(rulebook.rules),
        "extractor": "not-wired",
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    rulebook = load_rulebook()
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {"version": __version__, "rule_count": len(rulebook.rules)},
    )
