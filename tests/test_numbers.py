"""Alcohol-content parsing + the proof/ABV consistency check."""

from __future__ import annotations

from decimal import Decimal

import pytest

from label_assay.text.numbers import AlcoholContent, parse_alcohol_content


def test_parses_abv_and_proof_from_sample_label() -> None:
    # The spec's sample label: "45% Alc./Vol. (90 Proof)".
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


def test_impossible_abv_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        AlcoholContent(abv=Decimal("150"))
