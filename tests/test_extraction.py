"""Extraction layer: the deterministic fixture path, the blind contract, and a
live vision + OCR pass over a fixture label.

The live tests skip cleanly when no ANTHROPIC_API_KEY is configured, so the suite
stays green in CI without a key while still exercising the real path locally.
"""

from __future__ import annotations

import inspect
import io
import re

import pytest
from PIL import Image

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


def _solid_png(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_fixture_extractor_replays_by_pixel_content() -> None:
    from label_assay.extract.fixture import FixtureExtractor, fixture_key

    img = _solid_png((10, 20, 30))
    fixtures = {fixture_key(img): _extraction()}
    assert FixtureExtractor(fixtures).extract(img) == _extraction()


def test_fixture_extractor_raises_on_unknown_image() -> None:
    from label_assay.extract.fixture import FixtureExtractor

    with pytest.raises(KeyError):
        FixtureExtractor({}).extract(_solid_png((250, 250, 250)))


def test_fixture_key_survives_the_vision_reencode() -> None:
    # The service hands the extractor a fresh PNG encode (downscale_for_vision),
    # never the upload's own bytes — and two zlib builds can emit different PNG
    # streams for the same pixels, so a key over encoded bytes registered from
    # an upload missed the re-encode on another platform. The pixel key must
    # resolve both the pipeline's re-encode and a byte-distinct encode of the
    # same raster; the second leg fails under byte keying on every platform.
    from label_assay.extract.fixture import FixtureExtractor, fixture_key
    from label_assay.extract.images import downscale_for_vision

    upload = _fixture_bytes()
    extractor = FixtureExtractor({fixture_key(upload): _extraction()})
    assert extractor.extract(downscale_for_vision(upload)) == _extraction()

    buffer = io.BytesIO()
    Image.open(io.BytesIO(upload)).save(buffer, format="PNG", compress_level=1)
    variant = buffer.getvalue()
    assert variant != upload  # same pixels, different bytes
    assert extractor.extract(variant) == _extraction()


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
