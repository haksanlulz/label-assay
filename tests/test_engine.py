"""The compliance engine, end to end: an extraction + application -> a verdict.

These tests mutate a known-compliant extraction to exercise each verdict, and one
live test runs the whole pipeline (image -> vision extraction -> verdict).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from label_assay.config import get_settings
from label_assay.domain.models import Application, Verdict
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.ocr import OcrLine
from label_assay.rulebook.loader import load_rulebook
from label_assay.verify.confidence import unconfirmed_fields
from label_assay.verify.engine import infer_beverage_class, verify

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "bourbon_compliant.png"


def _warning_reference() -> str:
    rb = load_rulebook()
    return next(r for r in rb.rules if r.id == "health_warning_verbatim").match.reference


def _f(text: str | None) -> ExtractedField:
    return ExtractedField(verbatim=text, found=text is not None, value=text)


def _compliant_extraction() -> Extraction:
    return Extraction(
        brand_name=_f("OLD TOM DISTILLERY"),
        class_type=_f("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=_f("45% Alc./Vol. (90 Proof)"),
        net_contents=_f("750 mL"),
        government_warning=_f(_warning_reference()),
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


def test_beverage_class_inference() -> None:
    assert infer_beverage_class("Kentucky Straight Bourbon Whiskey") == "spirits"
    assert infer_beverage_class("California Red Wine") == "wine"
    assert infer_beverage_class("India Pale Ale") == "malt"


@pytest.mark.skipif(
    not SAMPLE.exists() or not get_settings().anthropic_api_key,
    reason="needs the sample image and ANTHROPIC_API_KEY",
)
def test_full_pipeline_image_to_verdict() -> None:
    from label_assay.extract.haiku import HaikuExtractor

    settings = get_settings()
    assert settings.anthropic_api_key is not None
    extraction = HaikuExtractor(api_key=settings.anthropic_api_key, model=settings.haiku_model).extract(
        SAMPLE.read_bytes()
    )
    report = verify(extraction, _application(), load_rulebook())
    # A compliant label must never FAIL; PASS expected, REVIEW tolerated.
    assert report.verdict in (Verdict.PASS, Verdict.NEEDS_REVIEW)
    assert report.verdict != Verdict.FAIL


# --- the legibility gate (confidence cross-check) ---


def _ocr_of_everything_but_brand() -> list[OcrLine]:
    return [
        OcrLine("Kentucky Straight Bourbon Whiskey", 0.95),
        OcrLine("45% Alc./Vol. (90 Proof)", 0.95),
        OcrLine("750 mL", 0.95),
        OcrLine(_warning_reference(), 0.95),
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


def test_dead_ocr_does_not_single_out_any_field() -> None:
    # An unreadable image must not manufacture per-field failures.
    assert unconfirmed_fields(_compliant_extraction(), [OcrLine("", 0.0)]) == set()


@pytest.mark.skipif(not SAMPLE.exists(), reason="run samples/make_samples.py first")
def test_bold_finding_runs_when_image_and_ocr_supplied() -> None:
    # Offline (no key): fixture extraction + real image + real OCR exercise the
    # bold check through the engine. A compliant label must not FAIL.
    from label_assay.extract.ocr import read_lines

    image = SAMPLE.read_bytes()
    report = verify(
        _compliant_extraction(),
        _application(),
        load_rulebook(),
        image=image,
        ocr_lines=read_lines(image),
    )
    bold = next((f for f in report.findings if f.rule_id == "health_warning_bold"), None)
    assert bold is not None
    assert bold.verdict != Verdict.FAIL
