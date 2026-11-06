"""Alcohol-content parsing + the proof/ABV consistency check."""

from __future__ import annotations

from decimal import Decimal

import pytest

from label_assay.text.numbers import AlcoholContent, AlcoholReading, parse_alcohol_content, read_alcohol_content


def test_parses_abv_and_proof_from_sample_label() -> None:
    # Sample label text: "45% Alc./Vol. (90 Proof)".
    ac = parse_alcohol_content("45% Alc./Vol. (90 Proof)")
    assert ac is not None
    assert ac.abv == Decimal("45")
    assert ac.proof == Decimal("90")
    assert ac.proof_matches_abv is True


def test_inconsistent_proof_is_flagged() -> None:
    ac = parse_alcohol_content("45% Alc./Vol. (100 Proof)")  # 45 x 2 != 100
    assert ac is not None
    assert ac.proof_matches_abv is False


def test_abv_only_has_no_proof_check() -> None:
    ac = parse_alcohol_content("13.5% ALC BY VOL")
    assert ac is not None
    assert ac.abv == Decimal("13.5")
    assert ac.proof is None
    assert ac.proof_matches_abv is None


def test_no_alcohol_content_returns_none() -> None:
    assert parse_alcohol_content("750 mL") is None
    assert parse_alcohol_content("") is None
    assert parse_alcohol_content(None) is None


def test_marketing_percent_is_not_truncated_into_an_abv() -> None:
    # "100%" must not parse as ABV 0 via its trailing "00%" — the real statement
    # sits later in the string.
    ac = parse_alcohol_content("TEQUILA 100% DE AGAVE 40% ALC./VOL. (80 PROOF)")
    assert ac is not None
    assert ac.abv == Decimal("40")
    assert ac.proof == Decimal("80")
    assert ac.proof_matches_abv is True

    ac = parse_alcohol_content("TEQUILA 100% AGAVE 40% ALC./VOL.")
    assert ac is not None
    assert ac.abv == Decimal("40")


def test_marketing_percent_with_only_a_proof_goes_unparsed() -> None:
    # No readable ABV: the engine routes this to review instead of failing a
    # compliant label on a fabricated ABV of 0.
    assert parse_alcohol_content("100% DE AGAVE 80 PROOF") is None


def test_leading_dot_decimal_is_not_misread_as_ten_times_itself() -> None:
    # ".5%" used to parse as 5; refusing to guess routes it to review upstream.
    assert parse_alcohol_content(".5% ALC/VOL") is None


def test_impossible_abv_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        AlcoholContent(abv=Decimal("150"))


@pytest.mark.parametrize("text", [
    "40% ALC/VOL (80 PROOF) (100 PROOF)",
    "40% ALC/VOL (80 PROOF) / 45% ALC/VOL (90 PROOF)",
    "ALCOHOL 40 PERCENT / ALCOHOL 45 PERCENT (80 PROOF)",
    "40% ALC/VOL (1080 PROOF)",
    "40% ALC/VOL (999 PROOF)",
    "40% ALC/VOL (80 PROOF) (1O0 PROOF)",
    "40% ALC/VOL (PROOF 80)",
    "40% ALC/VOL (-80 PROOF)",
    "40% ALC/VOL (.80 PROOF)",
    "40% ALC/VOL 100% ALC/VOL 80 PROOF",
    "40% ALC/VOL 999% ALC/VOL 80 PROOF",
    "40% ALC/VOL ALCOHOL 999 PERCENT 80 PROOF",
    "-40% ALC/VOL 80 PROOF",
    "40% ALC/VOL 80PROOF80",
    "40% ALC/VOL 4O% ALC/VOL 80 PROOF",
    "40% ALC/VOL 1OO% ABV 80 PROOF",
    "40% ALC/VOL ALCOHOL 4O PERCENT 80 PROOF",
])
def test_conflicting_or_unreadable_claims_are_not_silently_dropped(text: str) -> None:
    assert parse_alcohol_content(text) is None


@pytest.mark.parametrize("text", [
    "40% ALC/VOL (80 PROOF) / 40.0% ALC/VOL (80.0 PROOF)",
    "TEQUILA 100% DE AGAVE 40% ALC./VOL. (80 PROOF)",
    "40% ALC/VOL (80PROOF)",
    "40% ALC/VOL (80 PROOF), waterproof label",
])
def test_equivalent_repeated_and_existing_legitimate_values_remain_parseable(text: str) -> None:
    content = parse_alcohol_content(text)
    assert content is not None
    assert content.abv == Decimal("40")
    assert content.proof == Decimal("80")
    assert content.proof_matches_abv is True


@pytest.mark.parametrize(("text", "reason"), [
    ("40% ALC/VOL 45% ALC/VOL", "more than one alcohol by volume (40% and 45%)"),
    ("40% ALC/VOL (80 PROOF) 100 PROOF", "more than one proof (80 proof and 100 proof)"),
    ("40% ALC/VOL 4O% ALC/VOL", "alcohol content claim could not be read as a number"),
    ("40% ALC/VOL 8O PROOF", "proof claim could not be read as a number"),
    ("40% ALC/VOL 999 PROOF", "stated proof (999) is outside 0 to 200"),
    ("999% ALC/VOL", "stated alcohol by volume (999%) is outside 0 to 100"),
])
def test_an_unusable_claim_names_its_reason(text: str, reason: str) -> None:
    reading = read_alcohol_content(text)
    assert reading.content is None
    assert reading.reason is not None and reason in reading.reason
    assert parse_alcohol_content(text) is None


@pytest.mark.parametrize("text", ["", None, "750 mL"])
def test_nothing_stated_has_no_reason(text: str | None) -> None:
    assert read_alcohol_content(text) == AlcoholReading(None)
