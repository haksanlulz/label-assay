"""The test-label generator: determinism, manifest consistency, defect-taxonomy
coverage, application-CSV compatibility with the app's own parser, and every
manifest expected verdict checked against the engine itself.

Determinism is checked by comparing two in-process generations (the module's
corpus_dir fixture and one fresh run) — never against the committed fixtures,
whose bytes depend on the generating machine's fonts.
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
    corpus_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # corpus_dir was generated in this process with the same seed and count, so
    # one fresh run against it is the two-independent-generations comparison.
    fresh = tmp_path_factory.mktemp("labels_b")
    gen.generate(fresh, seed=gen.DEFAULT_SEED, count=gen.DEFAULT_COUNT)

    names_a = sorted(p.name for p in corpus_dir.iterdir())
    names_b = sorted(p.name for p in fresh.iterdir())
    assert names_a == names_b
    for name in names_a:
        assert (corpus_dir / name).read_bytes() == (fresh / name).read_bytes(), (
            f"{name} differs across runs"
        )


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
    # The body-caps rows are compliant (16.22(a)(2) fixes only the heading case),
    # so they count toward the compliant half of the corpus.
    assert counts.get("warning_body_caps", 0) == gen.N_BODY_CAPS
    compliant = counts["compliant"] + counts.get("warning_body_caps", 0)
    assert compliant >= len(specs) // 2  # about half the set is compliant


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
    # This assertion is strict (FAIL, not fail-or-review) because the fixtures
    # are rendered for a decisive measurement, not a borderline one: heading
    # and body share the same regular face at the same size (no bold file
    # involved), and the body words alone carry a 1px painted outline that
    # widens every body stroke by exactly 2px of pixel geometry (see
    # _wrap_warning and _NOT_BOLD_BODY_STROKE in tools/make_test_labels.py).
    # The heading is structurally the thinnest text in its own statement, so
    # the stroke ratio measures ~0.73-0.76 regardless of font file or OCR
    # geometry — deep inside the detector's conclusive not-bold band, where
    # platform jitter cannot move it across a band edge.
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
            f"{spec.filename}: bold finding is {bold.verdict.value} — the not-bold "
            "fixtures must fail the bold check outright"
        )
        assert report.verdict == Verdict.FAIL, (
            f"{spec.filename}: overall verdict is {report.verdict.value} on a not-bold label"
        )


def test_compliant_own_line_headings_are_cleared_cross_line_never_failed() -> None:
    from label_assay.extract.ocr import read_lines

    # The committed compliant-side fixtures through the real local pipeline
    # (real OCR + the engine with a perfect reader). Eight of the twelve print
    # the warning heading as its own OCR line and used to abstain to review on
    # every run; the size-normalized cross-line clearance now decides them.
    # Measured on the committed PNGs: cross-line ratios 1.18-2.02 against the
    # 1.15 floor.
    own_line = {
        "label_000.png", "label_005.png", "label_010.png", "label_011.png",
        "label_012.png", "label_013.png", "label_020.png", "label_023.png",
    }
    rulebook = load_rulebook()
    cleared: set[str] = set()
    seen: set[str] = set()
    for spec in fixture_corpus.corpus_specs():
        if spec.defect not in ("compliant", "warning_body_caps"):
            continue
        seen.add(spec.filename)
        image = fixture_corpus.fixture_path(spec).read_bytes()
        report = verify(
            fixture_corpus.perfect_extraction(spec),
            fixture_corpus.application_for(spec),
            rulebook,
            image=image,
            ocr_lines=read_lines(image),
        )
        bold = next(f for f in report.findings if f.rule_id == "health_warning_bold")
        # The hard invariant, both measurement paths: a compliant label's bold
        # heading is never failed. The cross-line path cannot emit a fail by
        # construction; the same-line path must not reach one either.
        assert bold.verdict != Verdict.FAIL, (
            f"{spec.filename}: bold check failed a compliant label ({bold.detail})"
        )
        if spec.filename in own_line and bold.verdict == Verdict.PASS:
            cleared.add(spec.filename)
    assert own_line <= seen  # the named set tracks the corpus, not a stale list
    # The pin is aggregate, not per-fixture: the normalized ratios transfer
    # across platforms but OCR box geometry does not exactly, and two fixtures
    # measure within a few hundredths of the floor (1.18 vs 1.15), so a
    # per-fixture pin would flake on CI. Locally all eight clear.
    assert len(cleared) >= 6, (
        f"only {len(cleared)} own-line headings cleared cross-line: {sorted(cleared)}"
    )


def test_committed_corpus_matches_the_generator(corpus_dir: Path) -> None:
    # Ties the committed tests/fixtures/labels to the current generator code, so
    # a generator change that drifts the corpus cannot leave stale fixtures in
    # the repo (PNG bytes are font/machine-dependent and are deliberately not
    # compared; the CSVs and the filename set are machine-independent).
    assert (corpus_dir / "manifest.csv").read_bytes() == fixture_corpus.MANIFEST.read_bytes()
    assert (corpus_dir / "applications.csv").read_bytes() == (
        fixture_corpus.LABELS_DIR / "applications.csv"
    ).read_bytes()
    generated = sorted(p.name for p in corpus_dir.glob("*.png"))
    committed = sorted(p.name for p in fixture_corpus.LABELS_DIR.glob("*.png"))
    assert generated == committed


def test_applications_csv_parses_with_the_apps_own_parser(corpus_dir: Path) -> None:
    raw = (corpus_dir / "applications.csv").read_bytes()
    applications = parse_application_csv(raw)
    png_names = {p.name for p in corpus_dir.glob("*.png")}
    assert set(applications) == png_names
    for application in applications.values():
        assert application.brand_name
        assert application.class_type
