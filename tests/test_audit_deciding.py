"""Regressions from the 2026-11-12 audit of the deciding layer. Each test
failed on the code as it stood at 054d521."""

from __future__ import annotations

from decimal import Decimal

import pytest

import fixture_corpus
from label_assay.domain.models import Application, Verdict
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.match.bold import is_warning_heading
from label_assay.match.warning import WarningVerdict, compare_warning
from label_assay.rulebook.loader import load_rulebook
from label_assay.text.numbers import read_alcohol_content
from label_assay.verify.confidence import scan_differences
from label_assay.verify.engine import verify

REFERENCE = fixture_corpus.mandated_warning()


def _f(text: str | None) -> ExtractedField:
    return ExtractedField(verbatim=text, found=text is not None, value=text)


def _extraction(warning: str = REFERENCE, alcohol: str = "45% Alc./Vol. (90 Proof)") -> Extraction:
    return Extraction(
        brand_name=_f("OLD TOM DISTILLERY"),
        class_type=_f("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=_f(alcohol),
        net_contents=_f("750 mL"),
        government_warning=_f(warning),
    )


def _scan(warning: str = REFERENCE, alcohol: str = "45% Alc./Vol. (90 Proof)") -> list[OcrLine]:
    return [OcrLine("OLD TOM DISTILLERY", 0.98), OcrLine("Kentucky Straight Bourbon Whiskey", 0.95),
            OcrLine(alcohol, 0.95), OcrLine("750 mL", 0.95), OcrLine(warning, 0.95)]


def _finding(rule_id: str, extraction: Extraction, scan: list[OcrLine] | None):
    report = verify(extraction, Application(brand_name="Old Tom Distillery", class_type="Bourbon Whiskey"),
                    load_rulebook(), ocr_lines=scan)
    return report, next(f for f in report.findings if f.rule_id == rule_id)


# --- a reader slip must not fail a label the scan read correctly (DECIDING-1) ---

@pytest.mark.parametrize("slip", [
    REFERENCE.replace("GOVERNMENT WARNING", "Government Warning"),  # reader wrote title case
    REFERENCE.replace("impairs", "impair"),                          # reader dropped a letter
])
def test_reader_slip_over_a_correctly_scanned_warning_is_a_review(slip: str) -> None:
    report, finding = _finding("health_warning_verbatim", _extraction(warning=slip), _scan())
    assert finding.verdict == Verdict.NEEDS_REVIEW
    assert "scan read the mandated statement word-for-word" in finding.detail
    assert finding.diff, "the reviewer still sees what the reader reported"
    assert report.verdict != Verdict.FAIL


def test_a_warning_both_reads_show_altered_still_fails() -> None:
    printed = REFERENCE.replace("impairs", "affects")
    _, finding = _finding("health_warning_verbatim", _extraction(warning=printed), _scan(warning=printed))
    assert finding.verdict == Verdict.FAIL


def test_title_case_heading_both_reads_show_still_fails() -> None:
    printed = REFERENCE.replace("GOVERNMENT WARNING", "Government Warning")
    _, finding = _finding("health_warning_verbatim", _extraction(warning=printed), _scan(warning=printed))
    assert finding.verdict == Verdict.FAIL


def test_engine_only_mode_keeps_the_reader_fail() -> None:
    _, finding = _finding("health_warning_verbatim", _extraction(warning=REFERENCE.replace("impairs", "affects")), None)
    assert finding.verdict == Verdict.FAIL


# --- decimal commas (DECIDING-2) ---

@pytest.mark.parametrize(("text", "abv", "proof"), [
    ("Alc. 11,5% by vol.", "11.5", None),
    ("40,0% ALC/VOL 80 PROOF", "40.0", "80"),
    ("13,5% vol", "13.5", None),
    ("ALCOHOL 12,5 PERCENT BY VOLUME", "12.5", None),
])
def test_decimal_comma_is_read_as_a_decimal(text: str, abv: str, proof: str | None) -> None:
    content = read_alcohol_content(text).content
    assert content is not None
    assert content.abv == Decimal(abv)
    assert content.proof == (Decimal(proof) if proof else None)


def test_a_comma_number_is_never_truncated_to_its_tail() -> None:
    # "1,000 PROOF" must not be read as 0 proof, nor "1,50%" style oddities as 50.
    assert read_alcohol_content("40% ALC/VOL 1,000 PROOF").content is None
    assert read_alcohol_content("40% ALC/VOL 1,000 PROOF").reason is not None


def test_decimal_comma_label_is_not_failed_and_agrees_with_a_dotted_scan() -> None:
    report, finding = _finding("alcohol_content_internally_consistent",
                               _extraction(alcohol="40,0% ALC/VOL 80 PROOF"), _scan(alcohol="40,0% ALC/VOL 80 PROOF"))
    assert finding.verdict == Verdict.PASS
    _, crossed = _finding("alcohol_content_internally_consistent",
                          _extraction(alcohol="40.0% ALC/VOL 80 PROOF"), _scan(alcohol="40,0% ALC/VOL 80 PROOF"))
    assert crossed.verdict == Verdict.PASS


def test_decimal_comma_disagreement_is_still_caught() -> None:
    _, finding = _finding("alcohol_content_internally_consistent",
                          _extraction(alcohol="40,0% ALC/VOL 80 PROOF"), _scan(alcohol="45,0% ALC/VOL 80 PROOF"))
    assert finding.verdict == Verdict.NEEDS_REVIEW


# --- heading locator (DECIDING-3) ---

@pytest.mark.parametrize("text", ["GOVRMENT WARNING", "SURGEONS WARNING", "WARNING", "A WARNING", "x" * 8 + "warning"])
def test_heading_locator_never_raises(text: str) -> None:
    assert is_warning_heading(text) in (True, False)


def test_heading_locator_tolerates_two_dropped_letters() -> None:
    assert is_warning_heading("GOVRMENT WARNING: (1)")
    assert not is_warning_heading("SURGEONS WARNING")


def test_heading_locator_looks_past_an_earlier_warning_word() -> None:
    assert is_warning_heading("ALLERGY WARNING SEE BACK GOVERNMENT WARNING: (1) ACCORDING")


# --- words added beside the statement (DECIDING-4) ---

@pytest.mark.parametrize("printed", [
    REFERENCE + " AND IS ALSO DELICIOUS.",
    "THIS IS NOT A REAL " + REFERENCE,
])
def test_recited_warning_over_added_words_is_held(printed: str) -> None:
    report, finding = _finding("health_warning_verbatim", _extraction(), _scan(warning=printed))
    assert finding.verdict == Verdict.NEEDS_REVIEW
    assert "other text on the same line" in finding.detail
    assert report.verdict != Verdict.PASS


def test_unrelated_neighbouring_lines_do_not_hold_a_compliant_warning() -> None:
    scan = _scan() + [OcrLine("CONTAINS SULFITES", 0.9), OcrLine("PRODUCT OF USA", 0.9)]
    _, finding = _finding("health_warning_verbatim", _extraction(), scan)
    assert finding.verdict == Verdict.PASS


def test_statement_split_across_scan_lines_still_passes() -> None:
    words = REFERENCE.split()
    lines = [" ".join(words[i:i + 7]) for i in range(0, len(words), 7)]
    scan = [OcrLine("OLD TOM DISTILLERY", 0.98), OcrLine("Kentucky Straight Bourbon Whiskey", 0.95),
            OcrLine("45% Alc./Vol. (90 Proof)", 0.95), OcrLine("750 mL", 0.95), *[OcrLine(t, 0.95) for t in lines]]
    _, finding = _finding("health_warning_verbatim", _extraction(), scan)
    assert finding.verdict == Verdict.PASS


# --- transcript punctuation and an empty reference (DECIDING-6) ---

def test_a_leading_quote_mark_in_the_transcript_is_not_an_alteration() -> None:
    assert compare_warning('"' + REFERENCE + '"', REFERENCE).verdict == WarningVerdict.MATCH


def test_an_empty_reference_is_refused_not_an_index_error() -> None:
    with pytest.raises(ValueError, match="reference"):
        compare_warning(REFERENCE, "")


# --- scan differences on a duplicated statement (DECIDING-10, -11) ---

def test_a_partial_copy_beside_a_full_one_yields_no_runaway_span() -> None:
    half = REFERENCE[: REFERENCE.index("(2)")].strip()
    differences = scan_differences(REFERENCE, [OcrLine(half, 0.9), OcrLine(REFERENCE.replace("birth", "berth"), 0.9)])
    assert all(len(found) <= 3 * len(expected) + 12 for expected, found in differences), differences
