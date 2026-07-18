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


def test_haiku_malformed_tool_payload_does_not_leak_the_transcription() -> None:
    # A malformed tool payload is possible even under tool_choice (the API forces
    # tool *use*, not a schema-valid input). Pydantic's ValidationError repr
    # embeds the offending input — here the transcribed label text — so the
    # extractor must re-raise without it, or service.py's logger.exception would
    # write the transcription to the server log on a schema drift.
    from label_assay.extract.haiku import HaikuExtractor

    secret = "STONE'S THROW SMALL BATCH BOURBON"
    malformed = {
        # 'found' is required and omitted -> ValidationError whose input is `secret`.
        "brand_name": {"verbatim": secret, "value": secret},
        "class_type": {"verbatim": None, "found": False, "value": None},
        "alcohol_content": {"verbatim": None, "found": False, "value": None},
        "net_contents": {"verbatim": None, "found": False, "value": None},
        "government_warning": {"verbatim": None, "found": False, "value": None},
    }

    class _Block:
        type = "tool_use"
        input = malformed

    class _FakeMessages:
        def create(self, **_kwargs):
            return type("_Resp", (), {"content": [_Block()]})()

    extractor = HaikuExtractor(api_key="test-key")
    extractor._client = type("_FakeClient", (), {"messages": _FakeMessages()})()

    with pytest.raises(RuntimeError) as excinfo:
        extractor.extract(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    message = str(excinfo.value)
    assert secret not in message  # the transcription never rides the error
    assert "brand_name" in message  # the schema location is safe to surface


def test_ocr_reads_a_fixture_label() -> None:
    from label_assay.extract.ocr import read_lines

    joined = " ".join(line.text for line in read_lines(_fixture_bytes()))
    squashed = re.sub(r"[^a-z0-9]", "", joined.casefold())
    assert "governmentwarning" in squashed  # the statutory heading is legible


@pytest.mark.skipif(not get_settings().anthropic_api_key, reason="needs ANTHROPIC_API_KEY")
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
