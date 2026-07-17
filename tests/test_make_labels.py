"""The test-label generator: determinism, manifest consistency, defect-taxonomy
coverage, application-CSV compatibility with the app's own parser, and every
manifest expected verdict checked against the engine itself.

Determinism is checked by generating twice in this test and comparing bytes —
never against the committed fixtures, whose bytes depend on the generating
machine's fonts.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import fixture_corpus
from label_assay.domain.models import Verdict
from label_assay.rulebook.loader import load_rulebook
from label_assay.verify.engine import verify
from label_assay.web.batch import parse_application_csv

gen = fixture_corpus.generator()


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("labels")
    gen.generate(out, seed=gen.DEFAULT_SEED, count=gen.DEFAULT_COUNT)
    return out


def test_same_seed_reproduces_byte_identical_output(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    a = tmp_path_factory.mktemp("labels_a")
    b = tmp_path_factory.mktemp("labels_b")
    gen.generate(a, seed=gen.DEFAULT_SEED, count=gen.DEFAULT_COUNT)
    gen.generate(b, seed=gen.DEFAULT_SEED, count=gen.DEFAULT_COUNT)

    names_a = sorted(p.name for p in a.iterdir())
    names_b = sorted(p.name for p in b.iterdir())
    assert names_a == names_b
    for name in names_a:
        assert (a / name).read_bytes() == (b / name).read_bytes(), f"{name} differs across runs"


def test_manifest_and_pngs_are_consistent(corpus_dir: Path) -> None:
    with (corpus_dir / "manifest.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    manifest_names = {row["filename"] for row in rows}
    png_names = {p.name for p in corpus_dir.glob("*.png")}
    assert manifest_names == png_names  # every row has its PNG, and vice versa
    assert all(row["expected_verdict"] in {"pass", "needs_review", "fail"} for row in rows)


def test_every_defect_type_appears_at_least_twice() -> None:
    specs = fixture_corpus.corpus_specs()
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec.defect] = counts.get(spec.defect, 0) + 1
    for defect in gen.DEFECTS:
        assert counts.get(defect, 0) >= 2, f"{defect} appears fewer than twice"
    assert counts["compliant"] >= len(specs) // 2  # about half the set is compliant


def test_brand_mismatch_covers_both_variants() -> None:
    # One typo-level filed brand (routes to review) and one different brand (fails).
    verdicts = {s.expected_verdict for s in fixture_corpus.corpus_specs() if s.defect == "brand_mismatch"}
    assert verdicts == {"needs_review", "fail"}


def test_variety_axes_are_covered() -> None:
    specs = fixture_corpus.corpus_specs()
    assert len({s.class_type for s in specs}) >= 8
    assert len({s.family for s in specs}) == 3  # spirits, wine, malt
    assert len({s.layout for s in specs}) >= 4
    assert len({s.palette for s in specs}) >= 6
    assert len({s.size for s in specs}) >= 4
    assert len({s.warning_placement for s in specs} - {"none"}) == 2  # bottom + column


def test_manifest_expected_verdicts_match_the_engine() -> None:
    # A perfect reader's extraction of each label, verified against its filed
    # application, must land exactly on the manifest's expected verdict. The
    # not-bold rows are excluded here because their defect lives in the rendered
    # pixels, not in the extracted text; they are asserted through the real
    # OCR + stroke-width path in the test below.
    rulebook = load_rulebook()
    for spec in fixture_corpus.corpus_specs():
        if spec.defect == "warning_not_bold":
            continue
        report = verify(
            fixture_corpus.perfect_extraction(spec),
            fixture_corpus.application_for(spec),
            rulebook,
        )
        assert report.verdict.value == spec.expected_verdict, (
            f"{spec.filename} ({spec.defect}): engine returned {report.verdict.value}, "
            f"manifest expects {spec.expected_verdict}"
        )


def test_not_bold_labels_fail_the_bold_check_on_their_rendered_pixels(corpus_dir: Path) -> None:
    from label_assay.extract.ocr import read_lines

    specs = [s for s in fixture_corpus.corpus_specs() if s.defect == "warning_not_bold"]
    assert specs  # a vacuous loop would silently assert nothing
    rulebook = load_rulebook()
    for spec in specs:
        image = (corpus_dir / spec.filename).read_bytes()
        report = verify(
            fixture_corpus.perfect_extraction(spec),
            fixture_corpus.application_for(spec),
            rulebook,
            image=image,
            ocr_lines=read_lines(image),
        )
        bold = next(f for f in report.findings if f.rule_id == "health_warning_bold")
        assert bold.verdict == Verdict.FAIL, (
            f"{spec.filename}: bold finding is {bold.verdict.value}, manifest expects fail"
        )
        assert report.verdict.value == spec.expected_verdict


def test_applications_csv_parses_with_the_apps_own_parser(corpus_dir: Path) -> None:
    raw = (corpus_dir / "applications.csv").read_bytes()
    applications = parse_application_csv(raw)
    png_names = {p.name for p in corpus_dir.glob("*.png")}
    assert set(applications) == png_names
    for application in applications.values():
        assert application.brand_name
        assert application.class_type
