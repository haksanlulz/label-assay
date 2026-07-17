"""Extraction layer: the deterministic fixture path, the blind contract, and a
live vision + OCR pass over a fixture label.

The live tests skip cleanly when no ANTHROPIC_API_KEY is configured, so the suite
stays green in CI without a key while still exercising the real path locally.
"""

from __future__ import annotations

import hashlib
import inspect
import re

import pytest

import fixture_corpus
from label_assay.config import get_settings
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.text.numbers import parse_alcohol_content

SPEC = fixture_corpus.known_good_compliant()
FIXTURE = fixture_corpus.fixture_path(SPEC)


def _fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


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


@pytest.mark.skipif(not FIXTURE.exists(), reason="run tools/make_test_labels.py first")
def test_ocr_reads_a_fixture_label() -> None:
    from label_assay.extract.ocr import read_lines

    joined = " ".join(line.text for line in read_lines(_fixture_bytes()))
    squashed = re.sub(r"[^a-z0-9]", "", joined.casefold())
    assert "governmentwarning" in squashed  # the statutory heading is legible


@pytest.mark.skipif(
    not FIXTURE.exists() or not get_settings().anthropic_api_key,
    reason="needs the fixture image and ANTHROPIC_API_KEY",
)
def test_haiku_extracts_expected_fields_from_fixture_label() -> None:
    import anthropic

    from label_assay.extract.haiku import HaikuExtractor

    settings = get_settings()
    assert settings.anthropic_api_key is not None
    extractor = HaikuExtractor(api_key=settings.anthropic_api_key, model=settings.haiku_model)
    try:
        result = extractor.extract(_fixture_bytes())
    except anthropic.AuthenticationError:
        pytest.skip("ANTHROPIC_API_KEY is invalid or expired")

    assert result.brand_name.found
    assert result.government_warning.found
    painted_abv = parse_alcohol_content(SPEC.alcohol_text)
    assert painted_abv is not None
    assert str(painted_abv.abv) in (result.alcohol_content.verbatim or "")
