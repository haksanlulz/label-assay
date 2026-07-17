"""Claude Haiku vision adapter.

Blind by construction: ``extract`` takes only the image, and the prompt is a
constant — no application data and no OCR text is ever interpolated into it, so
the model cannot "find" an expected answer. The schema forces the model to quote
the printed text (``verbatim``) before it commits to a value. Extended thinking
is off and the output schema is terse: output tokens dominate latency and this
must clear five seconds per label.

The tool schema is written out by hand (no ``$ref``/``$defs``) so it maps cleanly
onto the tool-use input_schema; the returned object is validated against the
Pydantic ``Extraction`` model, which stays the single definition of the shape.
"""

from __future__ import annotations

import base64

import anthropic

from label_assay.extract.base import Extraction

_PROMPT = (
    "You are assisting a TTB label examiner. Read this U.S. alcohol beverage "
    "label and extract only what is VISIBLE in the image. For each field: first "
    "quote the exact text as printed, then say whether it is present, then give "
    "the value. If a field is not visible, set found to false and verbatim to "
    "null. Do NOT guess and do NOT recite what such a label usually says. Report "
    "the government warning exactly as printed, including any deviation from the "
    "standard wording or capitalization."
)

_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "verbatim": {"type": ["string", "null"], "description": "Exact text as printed, or null if not visible."},
        "found": {"type": "boolean", "description": "True only if the field is visible on the label."},
        "value": {"type": ["string", "null"], "description": "The value, lightly cleaned; null if not found."},
    },
    "required": ["verbatim", "found", "value"],
}
_FIELDS = ("brand_name", "class_type", "alcohol_content", "net_contents", "government_warning")
_TOOL = {
    "name": "record_label_fields",
    "description": "Record the mandatory fields visible on the alcohol beverage label.",
    "input_schema": {
        "type": "object",
        "properties": {name: _FIELD_SCHEMA for name in _FIELDS},
        "required": list(_FIELDS),
    },
}


def _media_type(image: bytes) -> str:
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("unsupported image type (expected PNG, JPEG, or WEBP)")


class HaikuExtractor:
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001", timeout: float = 15.0) -> None:
        # A short timeout, because the SDK default is 10 minutes — fatal against a
        # 5-second budget. One retry on 429/5xx: the SDK default of two can hold
        # a single check for three consecutive timeouts.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self._model = model

    def extract(self, image: bytes) -> Extraction:
        data = base64.standard_b64encode(image).decode("ascii")
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": _TOOL["name"]},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": _media_type(image), "data": data},
                        },
                        {"type": "text", "text": _PROMPT},
                    ],
                }
            ],
        )
        for block in response.content:
            if block.type == "tool_use":
                return Extraction.model_validate(block.input)
        raise RuntimeError("model did not return the expected tool call")
