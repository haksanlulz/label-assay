"""The compliance engine, end to end: an extraction + application -> a verdict.

These tests mutate a known-compliant extraction to exercise each verdict, and one
live test runs the whole pipeline (image -> vision extraction -> verdict).
"""

from __future__ import annotations

import pytest

import fixture_corpus
from label_assay.config import get_settings
from label_assay.domain.models import Application, Verdict
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.rulebook.loader import Rulebook, load_rulebook
from label_assay.verify.confidence import unconfirmed_fields
from label_assay.verify.engine import infer_beverage_class, verify

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)


def _f(text: str | None) -> ExtractedField:
    return ExtractedField(verbatim=text, found=text is not None, value=text)


def _compliant_extraction() -> Extraction:
    return Extraction(
        brand_name=_f("OLD TOM DISTILLERY"),
        class_type=_f("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=_f("45% Alc./Vol. (90 Proof)"),
        net_contents=_f("750 mL"),
        government_warning=_f(fixture_corpus.mandated_warning()),
    )


def _application(brand: str = "Old Tom Distillery") -> Application:
    return Application(brand_name=brand, class_type="Kentucky Straight Bourbon Whiskey")


def test_compliant_label_passes_and_every_finding_is_cited() -> None:
    report = verify(_compliant_extraction(), _application(), load_rulebook())
    assert report.verdict == Verdict.PASS
    assert len(report.findings) >= 3  # warning, brand, abv
    assert all(f.citation.startswith("27 CFR") for f in report.findings)


def test_wrong_brand_fails() -> None:
    report = verify(_compliant_extraction(), _application(brand="Totally Different Bourbon"), load_rulebook())
    assert report.verdict == Verdict.FAIL
    assert any(
        f.rule_id == "brand_name_matches_application" and f.verdict == Verdict.FAIL
        for f in report.findings
    )


# --- a filing carries a brand and, optionally, a fanciful name ---


def _brand_finding(report):
    return next(f for f in report.findings if f.rule_id == "brand_name_matches_application")


def test_fanciful_name_alone_cannot_clear_a_different_filed_brand() -> None:
    # The real registry pair (cola_24071001001099): the can leads with the filed
    # fanciful name YELLOW CARD PILS; the filed brand is EARTHBOUND BEER. A
    # match to that name cannot prove the filed brand also appears.
    extraction = _compliant_extraction().model_copy(update={"brand_name": _f("yellow card pils")})
    application = Application(
        brand_name="Earthbound Beer", fanciful_name="YELLOW CARD PILS", class_type="BEER"
    )
    brand = _brand_finding(verify(extraction, application, load_rulebook()))
    assert brand.verdict == Verdict.NEEDS_REVIEW
    assert "fanciful name" in brand.detail


def test_brand_name_match_still_passes_when_a_fanciful_name_is_filed() -> None:
    application = Application(
        brand_name="Old Tom Distillery",
        fanciful_name="Frontier Reserve",
        class_type="Kentucky Straight Bourbon Whiskey",
    )
    brand = _brand_finding(verify(_compliant_extraction(), application, load_rulebook()))
    assert brand.verdict == Verdict.PASS
    assert "brand name" in brand.detail


def test_containment_of_the_fanciful_name_routes_to_review() -> None:
    # The read strictly contains the filed fanciful name and is unrelated to the
    # filed brand: close-but-related stays a person's call, on either name.
    extraction = _compliant_extraction().model_copy(update={"brand_name": _f("MANGOLORIAN IPA")})
    application = Application(
        brand_name="Mortalis Brewing Company", fanciful_name="MANGOLORIAN", class_type="ALE"
    )
    brand = _brand_finding(verify(extraction, application, load_rulebook()))
    assert brand.verdict == Verdict.NEEDS_REVIEW
    assert "fanciful name" in brand.detail


def test_matching_neither_filed_name_fails_naming_the_read_and_both_names() -> None:
    extraction = _compliant_extraction().model_copy(
        update={"brand_name": _f("Totally Different Bourbon")}
    )
    application = Application(
        brand_name="Earthbound Beer", fanciful_name="Yellow Card Pils", class_type="BEER"
    )
    brand = _brand_finding(verify(extraction, application, load_rulebook()))
    assert brand.verdict == Verdict.FAIL
    assert "Totally Different Bourbon" in brand.detail
    assert "Earthbound Beer" in brand.detail
    assert "Yellow Card Pils" in brand.detail


def test_altered_warning_fails() -> None:
    extraction = _compliant_extraction()
    altered = extraction.government_warning.verbatim.replace("birth defects", "defects")
    extraction = extraction.model_copy(update={"government_warning": _f(altered)})
    report = verify(extraction, _application(), load_rulebook())
    assert report.verdict == Verdict.FAIL
    assert any(f.rule_id == "health_warning_verbatim" and f.verdict == Verdict.FAIL for f in report.findings)


def test_inconsistent_proof_fails() -> None:
    extraction = _compliant_extraction().model_copy(
        update={"alcohol_content": _f("45% Alc./Vol. (100 Proof)")}  # 45 x 2 != 100
    )
    report = verify(extraction, _application(), load_rulebook())
    assert report.verdict == Verdict.FAIL


def test_missing_warning_needs_review_not_fail() -> None:
    # Absence can't be told from illegibility yet, so it must not auto-fail.
    extraction = _compliant_extraction().model_copy(
        update={"government_warning": ExtractedField(verbatim=None, found=False, value=None)}
    )
    report = verify(extraction, _application(), load_rulebook())
    assert report.verdict == Verdict.NEEDS_REVIEW


def test_all_abstentions_is_a_review_never_a_pass() -> None:
    # Zero positive evidence must not render "Compliant". Unreachable with the
    # shipped rulebook (the warning matcher always decides or reviews), so this
    # pins the aggregation itself: a rulebook whose only applicable rule
    # abstains — the bold check with no image — must aggregate to review.
    full = load_rulebook()
    bold_only = Rulebook(
        rules=[r for r in full.rules if r.id == "health_warning_bold"], version=full.version
    )
    assert bold_only.rules, "the bold rule vanished from the rulebook"
    report = verify(_compliant_extraction(), _application(), bold_only)
    assert {f.verdict for f in report.findings} == {Verdict.NOT_EVALUABLE}
    assert report.verdict == Verdict.NEEDS_REVIEW


def test_beverage_class_inference() -> None:
    assert infer_beverage_class("Kentucky Straight Bourbon Whiskey") == "spirits"
    assert infer_beverage_class("California Red Wine") == "wine"
    assert infer_beverage_class("India Pale Ale") == "malt"


@pytest.mark.live
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="needs ANTHROPIC_API_KEY")
def test_full_pipeline_image_to_verdict() -> None:
    import anthropic

    from label_assay.extract.haiku import HaikuExtractor

    settings = get_settings()
    assert settings.anthropic_api_key is not None
    try:
        extraction = HaikuExtractor(api_key=settings.anthropic_api_key, model=settings.haiku_model).extract(
            FIXTURE.read_bytes()
        )
    except anthropic.AuthenticationError:
        pytest.skip("ANTHROPIC_API_KEY is invalid or expired")
    report = verify(extraction, fixture_corpus.application_for(SPEC), load_rulebook())
    # A compliant label must never FAIL; PASS expected, REVIEW tolerated.
    assert report.verdict in (Verdict.PASS, Verdict.NEEDS_REVIEW)
    assert report.verdict != Verdict.FAIL


# --- the legibility gate (confidence cross-check) ---


def _ocr_of_everything_but_brand() -> list[OcrLine]:
    return [
        OcrLine("Kentucky Straight Bourbon Whiskey", 0.95),
        OcrLine("45% Alc./Vol. (90 Proof)", 0.95),
        OcrLine("750 mL", 0.95),
        OcrLine(fixture_corpus.mandated_warning(), 0.95),
    ]


def test_field_is_unconfirmed_when_ocr_does_not_show_it() -> None:
    unconfirmed = unconfirmed_fields(_compliant_extraction(), _ocr_of_everything_but_brand())
    assert "brand_name" in unconfirmed  # OCR never saw the brand text
    assert "class_type" not in unconfirmed  # OCR corroborates it


def test_unconfirmed_field_is_held_for_review_not_passed() -> None:
    # Brand PASSes on the extraction alone, but OCR can't corroborate it, so the
    # engine must hold it for review rather than pass it.
    report = verify(
        _compliant_extraction(),
        _application(),
        load_rulebook(),
        ocr_lines=_ocr_of_everything_but_brand(),
    )
    brand = next(f for f in report.findings if f.rule_id == "brand_name_matches_application")
    assert brand.verdict == Verdict.NEEDS_REVIEW
    assert report.verdict == Verdict.NEEDS_REVIEW


def test_corroborating_ocr_leaves_the_pass_intact() -> None:
    ocr = _ocr_of_everything_but_brand() + [OcrLine("OLD TOM DISTILLERY", 0.98)]
    report = verify(_compliant_extraction(), _application(), load_rulebook(), ocr_lines=ocr)
    assert report.verdict == Verdict.PASS


def test_corroborated_quote_does_not_clear_a_different_brand_value() -> None:
    extraction = _compliant_extraction().model_copy(update={
        "brand_name": ExtractedField(verbatim="OTHER BRAND", value="OLD TOM DISTILLERY", found=True)
    })
    lines = _ocr_of_everything_but_brand() + [OcrLine("OTHER BRAND", 0.99)]
    finding = _brand_finding(verify(extraction, _application(), load_rulebook(), ocr_lines=lines))
    assert finding.verdict == Verdict.NEEDS_REVIEW


def test_corroborated_brand_quote_cannot_be_shortened_to_a_pass() -> None:
    extraction = _compliant_extraction().model_copy(update={
        "brand_name": ExtractedField(verbatim="OLD CROW RESERVE", value="OLD CROW", found=True)
    })
    lines = _ocr_of_everything_but_brand() + [OcrLine("OLD CROW RESERVE", 0.99)]
    finding = _brand_finding(verify(extraction, _application("OLD CROW"), load_rulebook(), ocr_lines=lines))
    assert finding.verdict == Verdict.NEEDS_REVIEW


def test_value_without_a_quote_does_not_bypass_the_gate() -> None:
    # A vision model that fills value while leaving verbatim null is asserting a
    # read it never quoted — the exact hallucination shape the gate exists for.
    # The brand matcher consumes .value, so the gate must corroborate it too.
    extraction = _compliant_extraction().model_copy(
        update={
            "brand_name": ExtractedField(verbatim=None, found=True, value="Old Tom Distillery")
        }
    )
    report = verify(extraction, _application(), load_rulebook(), ocr_lines=_ocr_of_everything_but_brand())
    brand = next(f for f in report.findings if f.rule_id == "brand_name_matches_application")
    assert brand.verdict == Verdict.NEEDS_REVIEW
    assert report.verdict == Verdict.NEEDS_REVIEW


def test_value_without_a_quote_passes_when_ocr_corroborates_it() -> None:
    # The guard above must not over-gate: a value-only read the OCR does see is
    # corroborated like any other.
    extraction = _compliant_extraction().model_copy(
        update={
            "brand_name": ExtractedField(verbatim=None, found=True, value="Old Tom Distillery")
        }
    )
    ocr = _ocr_of_everything_but_brand() + [OcrLine("OLD TOM DISTILLERY", 0.98)]
    report = verify(extraction, _application(), load_rulebook(), ocr_lines=ocr)
    assert report.verdict == Verdict.PASS


def test_uncorroborated_fail_downgrades_to_review_not_fail() -> None:
    # The FAIL half of the legibility gate: a brand mismatch built on text the
    # OCR never saw must be held for a person, not shipped as a failure.
    report = verify(
        _compliant_extraction(),
        _application(brand="Totally Different Bourbon"),
        load_rulebook(),
        ocr_lines=_ocr_of_everything_but_brand(),
    )
    brand = next(f for f in report.findings if f.rule_id == "brand_name_matches_application")
    assert brand.verdict == Verdict.NEEDS_REVIEW
    assert "Unconfirmed" in brand.detail
    assert report.verdict != Verdict.FAIL


def test_zero_applicable_rules_is_a_review_never_a_pass() -> None:
    # "Compliant" over an empty findings list is the worst outcome for a
    # compliance tool; an empty rule set must route to a human.
    report = verify(_compliant_extraction(), _application(), Rulebook(rules=[], version="empty"))
    assert report.findings == []
    assert report.verdict == Verdict.NEEDS_REVIEW


def test_dead_ocr_holds_asserted_fields_for_review() -> None:
    assert "alcohol_content" in unconfirmed_fields(_compliant_extraction(), [OcrLine("", 0.0)])
    extraction = _compliant_extraction().model_copy(update={"alcohol_content": _f("45% 80 proof")})
    report = verify(extraction, _application(), load_rulebook(), ocr_lines=[])
    finding = next(f for f in report.findings if f.rule_id == "alcohol_content_internally_consistent")
    assert finding.verdict == Verdict.NEEDS_REVIEW
    # Explicitly omitted OCR remains available for engine-only callers.
    report = verify(extraction, _application(), load_rulebook(), ocr_lines=None)
    assert next(f for f in report.findings if f.rule_id == finding.rule_id).verdict == Verdict.FAIL


@pytest.mark.parametrize(("extracted", "observed"), [
    ("45% alc/vol 90 proof", "40% alc/vol 90 proof"),
    ("45% alc/vol 80 proof", "45% alc/vol 90 proof"),
    ("45% alc/vol 90 proof", "45 proof 90% alc/vol"),
    ("40-45% alc/vol 90 proof", "45-40% alc/vol 90 proof"),
])
def test_numeric_disagreement_is_never_decisive(extracted, observed) -> None:
    extraction = _compliant_extraction().model_copy(update={"alcohol_content": _f(extracted)})
    lines = _ocr_of_everything_but_brand() + [OcrLine("OLD TOM DISTILLERY", .99)]
    lines = [line for line in lines if "45%" not in line.text]
    lines.append(OcrLine(observed, .99))
    report = verify(extraction, _application(), load_rulebook(), ocr_lines=lines)
    finding = next(f for f in report.findings if f.rule_id == "alcohol_content_internally_consistent")
    assert finding.verdict == Verdict.NEEDS_REVIEW


@pytest.mark.parametrize(("extracted", "expected"), [
    ("45% alc/vol 90 proof", Verdict.PASS),
    ("45% alc/vol 80 proof", Verdict.FAIL),
])
def test_confirmed_numeric_evidence_retains_decisive_verdict(extracted, expected) -> None:
    extraction = _compliant_extraction().model_copy(update={"alcohol_content": _f(extracted)})
    lines = [OcrLine("OLD TOM DISTILLERY " + extracted, .99)]
    report = verify(extraction, _application(), load_rulebook(), ocr_lines=lines)
    finding = next(f for f in report.findings if f.rule_id == "alcohol_content_internally_consistent")
    assert finding.verdict == expected


# --- the recitation defense (a model quoting the statute over a different label) ---


def _ocr_with_warning(warning: str) -> list[OcrLine]:
    """A faithful OCR read of a label whose printed warning is ``warning``."""
    return [
        OcrLine("OLD TOM DISTILLERY", 0.98),
        OcrLine("Kentucky Straight Bourbon Whiskey", 0.95),
        OcrLine("45% Alc./Vol. (90 Proof)", 0.95),
        OcrLine("750 mL", 0.95),
        OcrLine(warning, 0.95),
    ]


def test_model_cannot_clear_capitals_ocr_reads_in_title_case() -> None:
    reference = fixture_corpus.mandated_warning()
    printed = reference.replace("GOVERNMENT WARNING", "Government Warning", 1)
    report = verify(_compliant_extraction(), _application(), load_rulebook(),
                    ocr_lines=_ocr_with_warning(printed))
    warning = next(f for f in report.findings if f.rule_id == "health_warning_verbatim")
    assert warning.verdict == Verdict.NEEDS_REVIEW


def test_real_ocr_blocks_recited_capitals_on_title_case_fixture() -> None:
    from label_assay.web.service import check_label

    spec = next(spec for spec in fixture_corpus.corpus_specs() if spec.filename == "label_008.png")
    extraction = fixture_corpus.perfect_extraction(spec).model_copy(
        update={"government_warning": _f(fixture_corpus.mandated_warning())})

    class Reader:
        def extract(self, _image):
            return extraction

    report = check_label(fixture_corpus.fixture_path(spec).read_bytes(),
                         fixture_corpus.application_for(spec), extractor=Reader()).report
    warning = next(f for f in report.findings if f.rule_id == "health_warning_verbatim")
    assert warning.verdict == Verdict.NEEDS_REVIEW


def test_recited_warning_over_an_altered_label_is_held_for_review() -> None:
    # The extraction quotes the canonical text (recitation); OCR reads the label
    # as printed, one word off. A fuzzy score calls that ~0.98 similar, so only
    # exact corroboration keeps it from auto-passing a non-compliant label.
    printed = fixture_corpus.mandated_warning().replace("impairs", "affects")
    report = verify(
        _compliant_extraction(), _application(), load_rulebook(), ocr_lines=_ocr_with_warning(printed)
    )
    warning = next(f for f in report.findings if f.rule_id == "health_warning_verbatim")
    assert warning.verdict == Verdict.NEEDS_REVIEW
    assert report.verdict != Verdict.PASS


def test_recited_warning_over_a_truncated_label_is_held_for_review() -> None:
    # The label prints only the first clause; the recited quote contains it
    # wholesale, which is the direction sliding-window scoring gets backwards.
    reference = fixture_corpus.mandated_warning()
    printed = reference[: reference.index("(2)")].strip()
    report = verify(
        _compliant_extraction(), _application(), load_rulebook(), ocr_lines=_ocr_with_warning(printed)
    )
    warning = next(f for f in report.findings if f.rule_id == "health_warning_verbatim")
    assert warning.verdict == Verdict.NEEDS_REVIEW
    assert report.verdict != Verdict.PASS


def test_warning_corroboration_is_exact_but_case_and_spacing_insensitive() -> None:
    from label_assay.verify.confidence import corroborates_exactly

    reference = fixture_corpus.mandated_warning()
    assert corroborates_exactly(reference, [OcrLine(reference, 0.9)])
    assert corroborates_exactly(reference, [OcrLine(reference.upper(), 0.9)])  # body caps are legal
    assert not corroborates_exactly(reference, [OcrLine(reference.replace("birth defects", "birth effects"), 0.9)])
    assert not corroborates_exactly(reference, [OcrLine("", 0.0)])


def test_field_support_penalizes_a_quote_longer_than_the_ocr_read() -> None:
    from label_assay.verify.confidence import _squash, field_support

    reference = fixture_corpus.mandated_warning()
    truncated_read = _squash(reference[: reference.index("(2)")])
    # The truncated read is a perfect substring of the recited quote; sliding
    # matching scores that 1.0 in the wrong direction.
    assert field_support(reference, truncated_read) < 0.95


@pytest.mark.live
@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="needs ANTHROPIC_API_KEY")
def test_altered_warning_labels_are_never_passed_live() -> None:
    # End to end with the real model: whether it transcribes the altered text
    # faithfully (FAIL) or recites the statute from memory (held for review by
    # the OCR corroboration), a PASS is never acceptable.
    import anthropic

    from label_assay.extract.haiku import HaikuExtractor
    from label_assay.extract.ocr import read_lines

    specs = [s for s in fixture_corpus.corpus_specs() if s.defect == "warning_altered_text"]
    assert specs
    settings = get_settings()
    extractor = HaikuExtractor(api_key=settings.anthropic_api_key, model=settings.haiku_model)
    for spec in specs:
        image = fixture_corpus.fixture_path(spec).read_bytes()
        try:
            extraction = extractor.extract(image)
        except anthropic.AuthenticationError:
            pytest.skip("ANTHROPIC_API_KEY is invalid or expired")
        report = verify(
            extraction,
            fixture_corpus.application_for(spec),
            load_rulebook(),
            image=image,
            ocr_lines=read_lines(image),
        )
        assert report.verdict != Verdict.PASS, f"{spec.filename} passed with an altered warning"


def test_bold_finding_runs_when_image_and_ocr_supplied() -> None:
    # Offline (no key): a perfect-reader extraction + real image + real OCR
    # exercise the bold check through the engine. A compliant label must not FAIL.
    report = verify(
        fixture_corpus.perfect_extraction(SPEC),
        fixture_corpus.application_for(SPEC),
        load_rulebook(),
        image=FIXTURE.read_bytes(),
        ocr_lines=fixture_corpus.fixture_ocr_lines(),
    )
    bold = next((f for f in report.findings if f.rule_id == "health_warning_bold"), None)
    assert bold is not None
    assert bold.verdict != Verdict.FAIL
