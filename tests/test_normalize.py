"""Normalizer behaviour + property tests.

The admissibility test is the load-bearing one: it proves every canon_statutory
step is a no-op on the mandated warning, which is why the verbatim comparison is
trustworthy.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from label_assay.rulebook.loader import load_rulebook
from label_assay.text.normalize import canon_brand, canon_statutory


def _reference() -> str:
    rb = load_rulebook()
    return next(r for r in rb.rules if r.id == "health_warning_verbatim").match.reference


def test_statutory_is_a_noop_on_the_canonical_reference() -> None:
    # Admissibility: canonicalizing the mandated text must not change it.
    ref = _reference()
    assert canon_statutory(ref) == ref


def test_statutory_collapses_ocr_whitespace_and_linebreak_hyphenation() -> None:
    ref = _reference()
    noisy = ref.replace(" ", "  ").replace("defects.", "de-\nfects.")
    assert canon_statutory(noisy) == canon_statutory(ref)


def test_statutory_preserves_case() -> None:
    # casefold would break the capitalization check, so it must not happen here.
    assert "GOVERNMENT WARNING" in canon_statutory("GOVERNMENT WARNING: hello")


def test_brand_stones_throw_collapses_to_equal() -> None:
    assert canon_brand("STONE'S THROW") == canon_brand("Stone's Throw")


def test_brand_strips_legal_suffix() -> None:
    assert canon_brand("Old Crow Distillery, Inc.") == canon_brand("Old Crow Distillery")


def test_brand_ampersand_and_diacritics() -> None:
    assert canon_brand("M&S") == canon_brand("M and S")
    assert canon_brand("RÉMY") == canon_brand("Remy")


_realistic = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x24F,
                           blacklist_categories=("Cc", "Cf", "Cs")),
    max_size=40,
)


@given(_realistic)
def test_statutory_is_idempotent(s: str) -> None:
    once = canon_statutory(s)
    assert canon_statutory(once) == once


@given(_realistic)
def test_brand_is_idempotent(s: str) -> None:
    once = canon_brand(s)
    assert canon_brand(once) == once
