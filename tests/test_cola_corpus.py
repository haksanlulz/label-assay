"""Integrity of the real-label corpus (tests/fixtures/cola): the files, the
applications CSV, and the provenance README agree with each other and with the
app's own upload constraints. No network and no OCR here — the labels are
exercised end-to-end by tools/eval_cola.py against a running instance.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from label_assay.web import app as webapp
from label_assay.web.batch import parse_application_csv

CORPUS = Path(__file__).resolve().parent / "fixtures" / "cola"
APPLICATIONS = CORPUS / "applications.csv"
README = CORPUS / "README.md"


def _png_names() -> set[str]:
    names = {p.name for p in CORPUS.glob("*.png")}
    assert names, "corpus PNGs are missing"  # a vacuous pass would hide an empty corpus
    return names


def test_every_csv_row_has_its_png_and_vice_versa() -> None:
    with APPLICATIONS.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {row["filename"] for row in rows} == _png_names()


def test_applications_csv_parses_with_the_apps_own_parser() -> None:
    applications = parse_application_csv(APPLICATIONS.read_bytes())
    assert set(applications) == _png_names()
    for application in applications.values():
        assert application.brand_name
        assert application.class_type


def test_every_png_passes_the_apps_upload_checks() -> None:
    for name in sorted(_png_names()):
        data = (CORPUS / name).read_bytes()
        assert data.startswith(webapp._MAGIC), f"{name} fails the magic-byte check"
        assert len(data) <= webapp._MAX_BYTES, f"{name} exceeds the single-file size limit"


def test_readme_documents_every_ttb_id() -> None:
    assert README.exists(), "the corpus provenance README is missing"
    text = README.read_text(encoding="utf-8")
    for name in sorted(_png_names()):
        match = re.fullmatch(r"cola_(\d+)\.png", name)
        assert match, f"{name} does not follow the cola_<TTBID>.png naming scheme"
        assert match.group(1) in text, f"README.md does not mention TTB ID {match.group(1)}"
