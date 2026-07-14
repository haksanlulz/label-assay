"""Batch processing — many labels through the engine with bounded concurrency.

The 5-second promise is a per-label interactive target; a batch is minutes, so
labels are fanned out (bounded) and their rows land as they finish, rather than
one request blocking on all of them. Job state is in-memory and single-instance
— fine for a prototype on one always-on machine; a production deployment would
use a shared job store.

Each label runs the label-internal checks. Brand-vs-application is a paired
single-label operation, so it is reported not-evaluable here: a batch of loose
images carries no per-label application to compare against.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from label_assay.domain.models import Application, LabelReport, Verdict
from label_assay.extract.base import ExtractorPort
from label_assay.web.service import ExtractionUnavailable, check_label

MAX_FILES = 50          # bounds cost + abuse on the public demo (see docs)
_CONCURRENCY = 6

_WORST_FIRST = {"fail": 0, "needs_review": 1, "not_evaluable": 2, "pass": 3}


@dataclass
class BatchItem:
    filename: str
    status: str = "pending"  # pending | done | error
    verdict: str | None = None
    detail: str | None = None


@dataclass
class BatchJob:
    id: str
    items: list[BatchItem] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def done(self) -> int:
        return sum(1 for i in self.items if i.status != "pending")

    def summary(self) -> dict[str, int]:
        counts = {"pass": 0, "needs_review": 0, "fail": 0, "error": 0, "not_evaluable": 0}
        for item in self.items:
            if item.status == "error":
                counts["error"] += 1
            elif item.verdict in counts:
                counts[item.verdict] += 1
        return counts


_JOBS: dict[str, BatchJob] = {}


def get_job(job_id: str) -> BatchJob | None:
    return _JOBS.get(job_id)


def create_job(filenames: list[str]) -> BatchJob:
    job = BatchJob(id=uuid.uuid4().hex[:12], items=[BatchItem(filename=n) for n in filenames])
    _JOBS[job.id] = job
    return job


def _headline(report: LabelReport) -> str:
    if report.verdict == Verdict.PASS:
        return "All automated checks passed."
    worst = min(report.findings, key=lambda f: _WORST_FIRST.get(f.verdict.value, 9), default=None)
    return worst.detail if worst else "No findings."


async def run_job(job: BatchJob, files: list[tuple[str, bytes]], extractor: ExtractorPort) -> None:
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def process(index: int, data: bytes) -> None:
        async with semaphore:
            item = job.items[index]
            try:
                report = await asyncio.to_thread(check_label, data, Application(), extractor=extractor)
                item.verdict = report.verdict.value
                item.detail = _headline(report)
                item.status = "done"
            except ExtractionUnavailable as exc:
                item.status, item.detail = "error", str(exc)
            except Exception:  # never let one bad file sink the batch
                item.status, item.detail = "error", "Could not process this file."

    await asyncio.gather(*(process(i, data) for i, (_name, data) in enumerate(files)))
