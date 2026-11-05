"""Stateless review forms: no model calls, stored uploads or replaced verdicts."""
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from label_assay import __version__
from label_assay.domain.review import CHOICES, ReviewSource, make_review
from label_assay.web.comparison import read_saved_report

router = APIRouter()
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_MAX_BYTES = 5 * 1024 * 1024


def page(request, *, source=None, values=None, error=None, status=200):
    return _TEMPLATES.TemplateResponse(request, "review.html", {
        "version": __version__, "source": source, "choices": CHOICES,
        "source_json": source.model_dump_json() if source else "",
        "values": values or {}, "error": error,
    }, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/review", response_class=HTMLResponse)
def review_form(request: Request):
    return page(request)


@router.post("/review/load", response_class=HTMLResponse)
async def review_load(request: Request, report_file: UploadFile, report_index: int = Form(0)):
    try:
        content = await report_file.read(_MAX_BYTES + 1)
        if len(content) > _MAX_BYTES:
            return page(request, error="Choose a report no larger than 5 MB.", status=413)
        selected = read_saved_report(content, report_index)
        selected["report"] = selected["report"].model_dump(mode="json")
        source = ReviewSource.model_validate(selected)
        return page(request, source=source)
    except (ValueError, RecursionError):
        return page(request, error="Choose a completed label row in a valid full-report JSON export.",
                    status=422)
    finally:
        await report_file.close()


@router.post("/review/export")
async def review_export(request: Request):
    source, values = None, {}
    try:
        async with request.form(max_files=0, max_fields=1003, max_part_size=_MAX_BYTES) as form:
            values = dict(form)
            if len(form.multi_items()) != len(values) or any(not isinstance(v, str) for v in values.values()):
                raise ValueError("Duplicate or invalid form fields.")
            raw = values.get("source_json", "")
            if len(raw.encode("utf-8")) > _MAX_BYTES:
                raise ValueError("Report too large.")
            source = ReviewSource.model_validate_json(raw)
            expected = {"source_json", "reviewer", "notes"}
            decisions = []
            for index, finding in enumerate(source.report.findings):
                expected.update({f"decision_{index}", f"note_{index}"})
                decisions.append({"rule_id": finding.rule_id,
                                  "choice": values.get(f"decision_{index}"),
                                  "note": values.get(f"note_{index}", "")})
            if set(values) != expected:
                raise ValueError("Missing or unexpected form fields.")
            record = make_review(source.model_dump(mode="json"), values["reviewer"],
                                 decisions, values["notes"])
        return JSONResponse(record.model_dump(mode="json"), headers={
            "Content-Disposition": 'attachment; filename="label-assay-review.json"',
            "Cache-Control": "no-store",
        })
    except (ValueError, RecursionError):
        return page(request, source=source, values=values, status=422,
                    error="Check the reviewer reference, decisions and note lengths. "
                    "Use the listed choices; reload the saved report if its contents changed.")
