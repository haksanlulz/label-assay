"""Local browser fixture: real routes/templates, synthetic completed reports, no AI calls.

Run with `uv run python tests/browser_server.py`. Not imported by the production app.
"""
from label_assay.domain.models import Finding, LabelReport, Verdict
from label_assay.web import app as webapp
from label_assay.web import batch

webapp._WARM_ON_STARTUP = False
job = batch.create_job(["earlier.png", "corrected.png", "incomplete.png"])
batch._JOBS.pop(job.id)
job.id = "browser-fixture"
batch._JOBS[job.id] = job
for item, verdict in zip(job.items, [Verdict.FAIL, Verdict.PASS, Verdict.NOT_EVALUABLE]):
    item.status = "done"
    item.verdict = "pass" if verdict == Verdict.NOT_EVALUABLE else verdict.value
    item.detail = "Synthetic browser test finding."
    item.checked_at = "2026-11-04T00:00:00+00:00"
    item.report = LabelReport(
        verdict=item.verdict,
        rulebook_version="browser-fixture",
        findings=[Finding(rule_id="sample", title="Sample heading", citation="Test citation",
                          verdict=verdict, detail=item.detail)],
    )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(webapp.app, host="127.0.0.1", port=18790)
