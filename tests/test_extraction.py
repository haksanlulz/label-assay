"""Extraction layer: the deterministic fixture path, the blind contract, and a
live vision + OCR pass over a synthetic label.

The live tests skip cleanly when no ANTHROPIC_API_KEY is configured, so the suite
stays green in CI without a key while still exercising the real path locally.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest

from label_assay.config import get_settings
from label_assay.extract.base import ExtractedField, Extraction

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "bourbon_compliant.png"


def _sample_bytes() -> bytes:
    return SAMPLE.read_bytes()


def _field(text: str) -> ExtractedField:
    return ExtractedField(verbatim=text, found=True, value=text)


def _extraction() -> Extraction:
    return Extraction(
        brand_name=_field("OLD TOM DISTILLERY"),
        class_type=_field("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=_field("45% Alc./Vol. (90 Proof)"),
        net_contents=_field("750 mL"),
        government_warning=_field("GOVERNMENT WARNING: ..."),
    )


def test_fixture_extractor_replays_by_hash() -> None:
    from label_assay.extract.fixture import FixtureExtractor

    img = b"pretend-image-bytes"
    fixtures = {hashlib.sha256(img).hexdigest(): _extraction()}
    assert FixtureExtractor(fixtures).extract(img) == _extraction()


def test_fixture_extractor_raises_on_unknown_image() -> None:
    from label_assay.extract.fixture import FixtureExtractor

    with pytest.raises(KeyError):
        FixtureExtractor({}).extract(b"unknown")


def test_haiku_extract_takes_only_an_image_and_uses_a_constant_prompt() -> None:
    # The blind contract: nothing about the application or OCR can reach the model.
    from label_assay.extract import haiku

    assert "{" not in haiku._PROMPT  # no interpolation placeholders
    params = list(inspect.signature(haiku.HaikuExtractor.extract).parameters)
    assert params == ["self", "image"]


@pytest.mark.skipif(not SAMPLE.exists(), reason="run samples/make_samples.py first")
def test_ocr_reads_the_sample_label() -> None:
    from label_assay.extract.ocr import read_lines

    joined = " ".join(line.text for line in read_lines(_sample_bytes())).lower()
    assert "bourbon" in joined or "distillery" in joined


@pytest.mark.skipif(
    not SAMPLE.exists() or not get_settings().anthropic_api_key,
    reason="needs the sample image and ANTHROPIC_API_KEY",
)
def test_haiku_extracts_expected_fields_from_sample_label() -> None:
    from label_assay.extract.haiku import HaikuExtractor

    settings = get_settings()
    assert settings.anthropic_api_key is not None
    extractor = HaikuExtractor(api_key=settings.anthropic_api_key, model=settings.haiku_model)
    result = extractor.extract(_sample_bytes())

    assert result.brand_name.found
    assert result.government_warning.found
    assert "bourbon" in (result.class_type.value or "").lower()
    assert "45" in (result.alcohol_content.verbatim or "")
