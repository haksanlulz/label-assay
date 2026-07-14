"""Brand matcher — the forgiving-but-not-too-forgiving check."""

from __future__ import annotations

from label_assay.match.brand import BrandVerdict, match_brand


def test_stones_throw_is_a_match() -> None:
    # The driving example from the stakeholder interviews.
    assert match_brand("STONE'S THROW", "Stone's Throw").verdict == BrandVerdict.MATCH


def test_legal_suffix_difference_is_a_match() -> None:
    assert match_brand("Old Crow Distillery, Inc.", "Old Crow Distillery").verdict == BrandVerdict.MATCH


def test_reserve_variant_is_a_mismatch_not_a_false_accept() -> None:
    # A subset-matching ratio (token_set_ratio) would score this 100. These are
    # different TTB products and must not auto-pass.
    assert match_brand("OLD CROW", "OLD CROW RESERVE").verdict == BrandVerdict.MISMATCH


def test_unrelated_brands_mismatch() -> None:
    assert match_brand("Tito's Handmade Vodka", "Grey Goose").verdict == BrandVerdict.MISMATCH


def test_single_ocr_slip_routes_to_review_not_mismatch() -> None:
    # OCR l->i confusion; a one-character slip should never hard-fail.
    result = match_brand("Jack Daniei's", "Jack Daniel's")
    assert result.verdict == BrandVerdict.REVIEW


def test_missing_brand_routes_to_review() -> None:
    assert match_brand(None, "Grey Goose").verdict == BrandVerdict.REVIEW
