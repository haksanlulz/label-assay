"""The extraction boundary: the port and the structured result it returns."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class ExtractedField(BaseModel):
    """One field read off the label.

    Evidence first: ``verbatim`` (the exact printed text) is declared before
    ``value`` so a schema-constrained model quotes what it sees before committing
    to an interpretation, and so the quote can later be checked against an
    independent OCR read. ``found`` is separate from ``value`` so the model is
    never pressured to fill a required slot when a field simply is not there.
    """

    verbatim: str | None = Field(description="Exact text as printed on the label, or null if not visible.")
    found: bool = Field(description="True only if the field is actually visible on the label.")
    value: str | None = Field(description="The value, lightly cleaned; null when not found.")


class Extraction(BaseModel):
    brand_name: ExtractedField
    class_type: ExtractedField
    alcohol_content: ExtractedField
    net_contents: ExtractedField
    government_warning: ExtractedField


class ExtractorPort(Protocol):
    """A label image in, a structured extraction out. No other inputs."""

    def extract(self, image: bytes) -> Extraction: ...
