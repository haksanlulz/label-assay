"""Shared access to the generated test-label corpus (tests/fixtures/labels).

Specs come from the generator itself (tools/make_test_labels.py) — the same
deterministic build that produced the committed PNGs — so tests read painted
ground truth from one place instead of hardcoding strings that could drift.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from label_assay.domain.models import Application
from label_assay.extract.base import ExtractedField, Extraction

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
LABELS_DIR = TESTS / "fixtures" / "labels"
MANIFEST = LABELS_DIR / "manifest.csv"

_LIGHT_PALETTES = {"white", "cream"}


def generator():
    """Import the generator script (tools/ is deliberately not a package)."""
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import make_test_labels

    return make_test_labels


@lru_cache(maxsize=1)
def corpus_specs():
    gen = generator()
    return gen.build_corpus(gen.DEFAULT_SEED, gen.DEFAULT_COUNT)


def known_good_compliant():
    """A compliant, light-palette fixture — the stable target for happy-path and
    live-extraction tests. The dark palettes are exercised by the corpus tests."""
    for spec in corpus_specs():
        if spec.defect == "compliant" and spec.palette in _LIGHT_PALETTES:
            return spec
    raise RuntimeError("no light-palette compliant fixture in the corpus")


def fixture_path(spec) -> Path:
    return LABELS_DIR / spec.filename


def perfect_extraction(spec) -> Extraction:
    """What a perfect reader would return for this label: the painted text, quoted."""

    def f(text: str | None) -> ExtractedField:
        return ExtractedField(verbatim=text, found=text is not None, value=text)

    return Extraction(
        brand_name=f(spec.painted_brand),
        class_type=f(spec.class_type),
        alcohol_content=f(spec.alcohol_text),
        net_contents=f(spec.net_contents),
        government_warning=f(spec.warning_text),
    )


def application_for(spec) -> Application:
    """The application filed for this label (differs from the painted brand on
    the brand-mismatch fixtures, by design)."""
    return Application(brand_name=spec.filed_brand, class_type=spec.class_type)
