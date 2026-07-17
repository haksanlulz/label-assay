"""Brand matcher — the forgiving-but-not-too-forgiving check."""

from __future__ import annotations

from label_assay.match.brand import BrandVerdict, match_brand


def test_stones_throw_is_a_match() -> None:
    # The driving example from the stakeholder interviews.
    assert match_brand("STONE'S THROW", "Stone's Throw").verdict == BrandVerdict.MATCH


def test_legal_suffix_difference_is_a_match() -> None:
    assert match_brand("Old Crow Distillery, Inc.", "Old Crow Distillery").verdict == BrandVerdict.MATCH


def test_reserve_variant_routes_to_review_never_an_auto_accept() -> None:
    # A subset-matching ratio (token_set_ratio) would score this 100 and
    # auto-pass two different TTB products. Containment instead abstains: close
    # but obviously related is a person's judgment, never an auto-verdict in
    # either direction.
    assert match_brand("OLD CROW", "OLD CROW RESERVE").verdict == BrandVerdict.REVIEW


def test_filed_name_extending_the_label_read_routes_to_review() -> None:
    # Real registry pair (cola_24064001000356): the keg collar reads MORTALIS,
    # the filed brand is MORTALIS BREWING COMPANY. A TTB-approved label must not
    # hard-fail on the brewery's own name around its brand.
    assert match_brand("MORTALIS", "MORTALIS BREWING COMPANY").verdict == BrandVerdict.REVIEW


def test_single_token_containment_routes_to_review_both_directions() -> None:
    # Real registry pair (cola_24093001000375): the filed brand is the single
    # character 7; the label art reads VODKA 7.
    assert match_brand("VODKA 7", "7").verdict == BrandVerdict.REVIEW
    assert match_brand("7", "VODKA 7").verdict == BrandVerdict.REVIEW


def test_label_read_extending_the_filed_name_routes_to_review() -> None:
    # Real registry pair (cola_24071001001099): the can wraps the filed brand
    # EARTHBOUND BEER in the taproom's full name.
    assert (
        match_brand("Anthonino's Taverna and Earthbound Beer", "EARTHBOUND BEER").verdict
        == BrandVerdict.REVIEW
    )


def test_containment_review_names_both_readings() -> None:
    # The detail is the reviewer's whole context: both normalized readings must
    # appear in it.
    finding = match_brand("MORTALIS", "MORTALIS BREWING COMPANY")
    assert "'mortalis'" in finding.detail and "'mortalis brewing'" in finding.detail


def test_shared_word_without_containment_is_still_a_mismatch() -> None:
    # Sharing a trade word is not containment; genuinely different names fail.
    assert match_brand("old tom distillery", "river bend distillery").verdict == BrandVerdict.MISMATCH


def test_unrelated_brands_mismatch() -> None:
    assert match_brand("Tito's Handmade Vodka", "Grey Goose").verdict == BrandVerdict.MISMATCH


def test_single_ocr_slip_routes_to_review_not_mismatch() -> None:
    # OCR l->i confusion; a one-character slip should never hard-fail.
    result = match_brand("Jack Daniei's", "Jack Daniel's")
    assert result.verdict == BrandVerdict.REVIEW


def test_missing_brand_routes_to_review() -> None:
    assert match_brand(None, "Grey Goose").verdict == BrandVerdict.REVIEW
