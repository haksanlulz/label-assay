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
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from label_assay.domain.models import Application, LabelReport, Verdict
from label_assay.extract.base import ExtractorPort
from label_assay.web.budget import DailyBudget
from label_assay.web.service import ExtractionUnavailable, check_label

# A file count is not the real constraint, so it is not the real bound. What
# actually limits a batch is memory (total upload size) and money (the daily
# budget), and both are enforced separately. This is a sanity ceiling with room
# well above the stated 200-300 peak, so a larger test run is answered rather
# than refused.
MAX_FILES = 1000
# Total upload size: at the 5 MB per-file limit, a few hundred files would be
# gigabytes, and no single machine should hold that in memory at once.
MAX_TOTAL_BYTES = 150 * 1024 * 1024
# The applications CSV for a few hundred labels is tens of kilobytes; matching
# the per-image cap gives ~200x headroom while keeping the read bounded.
MAX_CSV_BYTES = 5 * 1024 * 1024
_CONCURRENCY = 6

logger = logging.getLogger(__name__)

_WORST_FIRST = {"fail": 0, "needs_review": 1, "not_evaluable": 2, "pass": 3}


class ApplicationCSVError(ValueError):
    """The applications upload is not readable as CSV (a binary file picked by
    mistake, for example). The message is safe to show a user."""


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
    # CSV pairing observability: None when no applications CSV was uploaded;
    # otherwise how many rows parsed and how many uploaded labels found no row.
    csv_rows: int | None = None
    csv_unmatched: int | None = None

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
        # A PASS with abstentions is not "all checks passed" — the common batch
        # case is a label with no application row, where the brand comparison
        # never ran. Say so, using the finding's own user-readable detail.
        skipped = [f for f in report.findings if f.verdict is Verdict.NOT_EVALUABLE]
        if not skipped:
            return "All automated checks passed."
        if len(skipped) == 1:
            return f"All checks that could run passed. Not checked: {skipped[0].detail}"
        return f"All checks that could run passed; {len(skipped)} checks could not be evaluated."
    worst = min(report.findings, key=lambda f: _WORST_FIRST.get(f.verdict.value, 9), default=None)
    return worst.detail if worst else "No findings."


def pairing_key(filename: str) -> str:
    """The case- and path-insensitive form used to pair a label with its CSV
    row. Importers' spreadsheets disagree with filesystems about case and folder
    prefixes often enough that an exact string match silently unpairs real
    batches ("Label1.PNG" vs "label1.png", "labels/x.png" vs "x.png")."""
    return PurePosixPath(filename.replace("\\", "/")).name.casefold()


def parse_application_csv(data: bytes) -> dict[str, Application]:
    """Map pairing_key(filename) -> the application filed for it.

    Expected columns: filename, brand_name, class_type (fanciful_name optional).
    Headers are matched case-insensitively. A CSV whose headers carry no
    filename column raises — accepting it would silently ignore the whole file
    and abstain on every brand comparison with no hint why. A row without a
    filename is skipped rather than failing the whole batch.
    """
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    applications: dict[str, Application] = {}
    # Binary content (an image or spreadsheet picked into the CSV field) makes
    # the csv module itself raise — reading fieldnames consumes the first row,
    # so that access sits inside the try as well.
    try:
        if not reader.fieldnames:
            return {}
        header = [(name or "").strip().lower() for name in reader.fieldnames]
        # Undecodable or control bytes in the header row mean a binary file, not
        # a CSV with the wrong columns — say so instead of "no filename column".
        if any("�" in name or any(ord(ch) < 32 for ch in name) for name in header):
            raise ApplicationCSVError(
                "That applications file could not be read as a CSV. Please export "
                "the spreadsheet as a .csv file and try again."
            )
        reader.fieldnames = header
        if "filename" not in header:
            raise ApplicationCSVError(
                "The applications file has no 'filename' column. Expected columns: "
                "filename, brand_name, class_type."
            )

        for row in reader:
            filename = (row.get("filename") or "").strip()
            if not filename:
                continue
            applications[pairing_key(filename)] = Application(
                brand_name=(row.get("brand_name") or "").strip(),
                class_type=(row.get("class_type") or "").strip(),
                # Optional column: an absent header and an empty cell both mean
                # no fanciful name was filed.
                fanciful_name=(row.get("fanciful_name") or "").strip(),
            )
    except csv.Error as exc:
        raise ApplicationCSVError(
            "That applications file could not be read as a CSV. Please export the "
            "spreadsheet as a .csv file and try again."
        ) from exc
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
            application = applications.get(pairing_key(name), Application())
            try:
                report = await asyncio.to_thread(
                    check_label, data, application, extractor=extractor, budget=budget
                )
                item.verdict = report.verdict.value
                item.detail = _headline(report)
                item.status = "done"
            except ExtractionUnavailable as exc:
                # Expected degradation (no key, budget, reader down) — but the
                # chained cause is the only server-side trace, so record it.
                logger.warning("Batch item %r: %s", name, exc, exc_info=exc)
                item.status, item.detail = "error", str(exc)
            except Exception:  # never let one bad file sink the batch
                # A genuine pipeline bug lands here looking identical to a bad
                # file; the log record is what tells them apart.
                logger.exception("Batch item %r: unhandled error", name)
                item.status, item.detail = "error", "Could not process this file."

    await asyncio.gather(*(process(i, name, data) for i, (name, data) in enumerate(files)))
