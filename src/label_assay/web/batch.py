"""Batch processing — many labels through the engine with bounded concurrency.

The 5-second promise is a per-label interactive target; a batch is minutes, so
labels are fanned out (bounded) and their rows land as they finish, rather than
one request blocking on all of them. Job state is in-memory and single-instance
— fine for a prototype on one always-on machine; a production deployment would
use a shared job store.

A batch is labels *plus the data filed on their applications* — importers submit
applications, not loose artwork. The application data arrives as a CSV keyed by
filename, so each label is checked against its own filed brand and class. Labels
with no matching CSV row still run every label-internal check; only the
brand-vs-application comparison reports not-evaluable for those.
"""

from __future__ import annotations

import asyncio
import csv
import io
import uuid
from dataclasses import dataclass, field

from label_assay.domain.models import Application, LabelReport, Verdict
from label_assay.extract.base import ExtractorPort
from label_assay.web.budget import DailyBudget
from label_assay.web.service import ExtractionUnavailable, check_label

# The stakeholder ask is a peak-season dump of 200-300 applications at once, so
# that is the ceiling. Spend is bounded by the daily budget guard, not by an
# arbitrarily small file cap — the two are different concerns.
MAX_FILES = 300
# Total upload size: 300 files at the 5 MB per-file limit would be 1.5 GB, which
# no single machine should hold in memory at once.
MAX_TOTAL_BYTES = 150 * 1024 * 1024
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


def parse_application_csv(data: bytes) -> dict[str, Application]:
    """Map filename -> the application filed for it.

    Expected columns: filename, brand_name, class_type (fanciful_name optional).
    Headers are matched case-insensitively; unknown columns are ignored, and a row
    without a filename is skipped rather than failing the whole batch.
    """
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return {}
    reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames]

    applications: dict[str, Application] = {}
    for row in reader:
        filename = (row.get("filename") or "").strip()
        if not filename:
            continue
        applications[filename] = Application(
            brand_name=(row.get("brand_name") or "").strip(),
            class_type=(row.get("class_type") or "").strip(),
            fanciful_name=(row.get("fanciful_name") or "").strip() or None,
        )
    return applications


async def run_job(
    job: BatchJob,
    files: list[tuple[str, bytes]],
    extractor: ExtractorPort,
    budget: DailyBudget | None = None,
    applications: dict[str, Application] | None = None,
) -> None:
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    applications = applications or {}

    async def process(index: int, name: str, data: bytes) -> None:
        async with semaphore:
            item = job.items[index]
            # An unmatched label still gets every label-internal check; only the
            # brand comparison abstains.
            application = applications.get(name, Application())
            try:
                report = await asyncio.to_thread(
                    check_label, data, application, extractor=extractor, budget=budget
                )
                item.verdict = report.verdict.value
                item.detail = _headline(report)
                item.status = "done"
            except ExtractionUnavailable as exc:
                item.status, item.detail = "error", str(exc)
            except Exception:  # never let one bad file sink the batch
                item.status, item.detail = "error", "Could not process this file."

    await asyncio.gather(*(process(i, name, data) for i, (name, data) in enumerate(files)))
