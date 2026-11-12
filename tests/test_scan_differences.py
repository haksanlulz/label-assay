"""When the reader's transcription matches the mandated warning but the
independent scan does not confirm it, the held finding says which words the
scan read differently. The verdict stays a review: a scan misreads small print,
so its read alone never fails a label, but it must not be thrown away either.

Found on a TTB-approved filing (cola_24100001000120) whose printed heading
reads GOVERMENT WARNING: the reader recited the correct text, the scan read the
misspelling at 0.98 confidence, and the finding told the reviewer nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fixture_corpus
from label_assay.domain.models import Application, Verdict
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.rulebook.loader import load_rulebook
from label_assay.verify.confidence import scan_differences
from label_assay.verify.engine import verify

REFERENCE = fixture_corpus.mandated_warning()
COLA = Path(__file__).parent / "fixtures" / "cola" / "cola_24100001000120.png"

# The scan's own lines around the statement on that filing, as RapidOCR
# returned them on 2026-11-11: words run together, a certifier's seal read in
# between the statement's lines, one genuine misprint (GOVERMENT) and two
# misreads (BEGAUSE, ABILTY).
ALSINA_SCAN = [
    "ALSINASARDA",
    "WINEMADEFROM ORGANIC GRAPES-CERTIFIED ORGANICBY CCFAE",
    "GOVERMENT WARNING: (1) ACCORDING TO THE",
    "PRODUCCIO",
    "SURGEONGENERAL,WOMENSHOULDNOTDRINK",
    "COHSELL DELA",
    "AGRARIAECOLOGICA",
    "ALCOHOLICBEVERAGES DURINGPREGNANCY",
    "BEGAUSEOFTHERISKOFBIRTHDEFECTS",
    "(2)CONSUMPTIONOF ALCOHOLIC BEVERAGES",
    "IMPAIRSYOURABILTYTODRIVEACAROROPERATE",
    "CCPAE",
    "MACHINERY,AND MAY CAUSEHEALTHPROBLEMS.",
    "CONTAINSSULFTTES",
    "750 ml",
    "Alc. 11,5% by vol.",
]


def _f(text: str | None) -> ExtractedField:
    return ExtractedField(verbatim=text, found=text is not None, value=text)


def _reciting_extraction() -> Extraction:
    return Extraction(
        brand_name=_f("ALSINA & SARDA"),
        class_type=_f("CAVA"),
        alcohol_content=_f("Alc. 11,5% by vol."),
        net_contents=_f("750 ml"),
        government_warning=_f(REFERENCE),
    )


def _lines(texts: list[str]) -> list[OcrLine]:
    return [OcrLine(text, 0.97) for text in texts]


def _warning(lines: list[OcrLine]):
    report = verify(_reciting_extraction(), Application(brand_name="ALSINA & SARDA", class_type="SPARKLING WINE"),
                    load_rulebook(), ocr_lines=lines)
    return next(f for f in report.findings if f.rule_id == "health_warning_verbatim")


def test_a_misspelled_heading_is_named() -> None:
    printed = REFERENCE.replace("GOVERNMENT", "GOVERMENT")
    assert scan_differences(REFERENCE, _lines([printed])) == [("GOVERNMENT", "GOVERMENT")]


def test_a_changed_word_is_named_with_what_the_scan_read() -> None:
    printed = REFERENCE.replace("impairs", "affects")
    assert scan_differences(REFERENCE, _lines([printed])) == [("impairs", "affects")]


def test_run_together_scan_lines_still_name_the_words() -> None:
    # The seal's lines are not the statement's and are not reported as additions.
    assert scan_differences(REFERENCE, _lines(ALSINA_SCAN)) == [
        ("GOVERNMENT", "GOVERMENT"), ("because", "BEGAUSE"), ("ability", "ABILTY"),
    ]


def test_a_line_the_scan_never_read_is_one_unread_span() -> None:
    lines = [text for text in ALSINA_SCAN if not text.startswith("IMPAIRS")]
    assert scan_differences(REFERENCE, _lines(lines)) == [
        ("GOVERNMENT", "GOVERMENT"), ("because", "BEGAUSE"),
        ("impairs your ability to drive a car or operate", ""),
    ]


def test_a_word_added_inside_a_line_is_reported() -> None:
    printed = REFERENCE.replace("should not drink", "should not ever drink")
    assert scan_differences(REFERENCE, _lines([printed])) == [("not", "notever")]


def test_a_faithful_scan_has_no_differences() -> None:
    assert scan_differences(REFERENCE, _lines(["OLD TOM", REFERENCE.upper(), "750 mL"])) == []


def test_a_scan_with_no_warning_in_it_offers_nothing() -> None:
    # Nothing resembling the statement was read; naming every word as
    # "different" would be noise, not evidence.
    assert scan_differences(REFERENCE, _lines(["ALSINA & SARDA", "BRUT NATURE VINTAGE", "750 ml"])) == []
    assert scan_differences(REFERENCE, []) == []


def test_a_truncated_warning_is_reported_as_unread_text() -> None:
    printed = REFERENCE[: REFERENCE.index("(2)")].strip()
    differences = scan_differences(REFERENCE, _lines([printed]))
    assert differences == []  # the closing words are absent: no anchor, so no claim


def test_the_held_finding_names_the_words_and_stays_a_review() -> None:
    finding = _warning(_lines(ALSINA_SCAN))
    assert finding.verdict == Verdict.NEEDS_REVIEW
    assert "“GOVERNMENT” as “GOVERMENT”" in finding.detail
    assert "“because” as “BEGAUSE”" in finding.detail
    assert "A scan can misread" in finding.detail
    unread = _warning(_lines([text for text in ALSINA_SCAN if not text.startswith("IMPAIRS")]))
    assert "did not read “impairs your ability to drive a car or operate”" in unread.detail
    assert finding.diff == []  # diff is for confirmed differences only


def test_the_held_finding_is_unchanged_when_the_scan_offers_nothing() -> None:
    finding = _warning(_lines(["ALSINA & SARDA", "Alc. 11,5% by vol.", "750 ml", "CAVA BRUT NATURE"]))
    assert finding.verdict == Verdict.NEEDS_REVIEW
    assert finding.detail.endswith("so a person should verify it.")
    assert "differently" not in finding.detail


def test_a_long_list_of_differences_is_capped() -> None:
    printed = REFERENCE
    for right, wrong in (("Surgeon", "Surgeom"), ("pregnancy", "pregnamcy"), ("defects", "defecls"),
                         ("Consumption", "Consumptlon"), ("ability", "abilty"), ("machinery", "machimery")):
        assert right in printed
        printed = printed.replace(right, wrong)
    finding = _warning(_lines(["ALSINA & SARDA", "Alc. 11,5% by vol.", printed]))
    assert finding.verdict == Verdict.NEEDS_REVIEW
    assert finding.detail.count(" as “") == 4
    assert "differed in 2 more places" in finding.detail


@pytest.mark.live
@pytest.mark.xfail(strict=False, raises=AssertionError, reason=(
    "Known limit, measured 2026-11-12: the reader writes GOVERNMENT for the printed "
    "GOVERMENT, 3 of 3 on claude-haiku-4-5 with the shipped prompt, 3 of 3 with a "
    "letter-by-letter instruction added, 3 of 3 on claude-haiku-5-5. The scan note "
    "above is what tells the reviewer. An XPASS here means a reader finally copied it."
))
def test_reader_transcribes_the_misprinted_heading_as_printed() -> None:
    # The reader's job is to copy; correcting GOVERMENT to GOVERNMENT turns a
    # wording failure into a review. One paid call.
    from label_assay.config import get_settings
    from label_assay.extract.haiku import HaikuExtractor

    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("needs ANTHROPIC_API_KEY")
    extraction = HaikuExtractor(api_key=settings.anthropic_api_key, model=settings.haiku_model).extract(
        COLA.read_bytes()
    )
    quoted = (extraction.government_warning.verbatim or "").upper()
    assert "GOVERMENT WARNING" in quoted, quoted[:60]


@pytest.mark.slow
def test_bold_check_finds_a_misspelled_heading() -> None:
    # 16.22(a)(2) is about the heading's weight; a misprinted letter in it is
    # 16.21's finding and must not stop the weight from being looked at.
    from label_assay.extract.ocr import read_lines
    from label_assay.match.bold import check_warning_bold

    image = COLA.read_bytes()
    finding = check_warning_bold(image, read_lines(image))
    assert "Could not locate" not in finding.detail


def test_heading_locator_accepts_near_spellings_only() -> None:
    from label_assay.match.bold import is_warning_heading

    assert is_warning_heading("GOVERNMENT WARNING: (1) ACCORDING")
    assert is_warning_heading("GOVERMENTWARNING:(1)")
    assert is_warning_heading("Goverment Warning")
    assert not is_warning_heading("STORM WARNING: HIGH SEAS")
    assert not is_warning_heading("WARNING: CONTAINS SULFITES")
    assert not is_warning_heading("GOVERNMENT OF CANADA")


@pytest.mark.slow
def test_real_scan_of_the_misprinted_filing_names_the_heading() -> None:
    from label_assay.extract.ocr import read_lines

    finding = _warning(read_lines(COLA.read_bytes()))
    assert finding.verdict == Verdict.NEEDS_REVIEW
    assert "“GOVERNMENT” as “GOVERMENT”" in finding.detail
