"""The daily spend guard — the app-side bound on what a public demo can cost."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pytest

from label_assay.domain.models import Application
from label_assay.extract.base import ExtractedField, Extraction
from label_assay.extract.fixture import FixtureExtractor
from label_assay.rulebook.loader import load_rulebook
from label_assay.web.budget import EST_COST_PER_LABEL_USD, BudgetExhausted, DailyBudget
from label_assay.web.service import ExtractionUnavailable, check_label

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "bourbon_compliant.png"


def test_reserve_accumulates_and_then_refuses() -> None:
    budget = DailyBudget(limit_usd=EST_COST_PER_LABEL_USD * 2)
    budget.reserve()
    budget.reserve()
    assert budget.spent_usd == pytest.approx(EST_COST_PER_LABEL_USD * 2)
    with pytest.raises(BudgetExhausted):
        budget.reserve()


def test_a_new_day_resets_the_tally() -> None:
    budget = DailyBudget(limit_usd=EST_COST_PER_LABEL_USD)
    day_one = dt.date(2026, 7, 14)
    budget.reserve(today=day_one)
    with pytest.raises(BudgetExhausted):
        budget.reserve(today=day_one)
    budget.reserve(today=day_one + dt.timedelta(days=1))  # tomorrow is fine
    assert budget.spent_usd == pytest.approx(EST_COST_PER_LABEL_USD)


def test_zero_budget_refuses_immediately() -> None:
    with pytest.raises(BudgetExhausted):
        DailyBudget(limit_usd=0.0).reserve()


@pytest.mark.skipif(not SAMPLE.exists(), reason="run samples/make_samples.py first")
def test_exhausted_budget_stops_the_reader_before_it_is_called() -> None:
    # An exhausted budget must refuse before any paid call, and surface as a
    # clean user-facing message rather than an internal error.
    image = SAMPLE.read_bytes()

    def f(text: str) -> ExtractedField:
        return ExtractedField(verbatim=text, found=True, value=text)

    extraction = Extraction(
        brand_name=f("OLD TOM DISTILLERY"),
        class_type=f("Kentucky Straight Bourbon Whiskey"),
        alcohol_content=f("45% Alc./Vol. (90 Proof)"),
        net_contents=f("750 mL"),
        government_warning=f(
            next(r for r in load_rulebook().rules if r.id == "health_warning_verbatim").match.reference
        ),
    )
    fixture = FixtureExtractor({hashlib.sha256(image).hexdigest(): extraction})

    with pytest.raises(ExtractionUnavailable, match="daily limit"):
        check_label(image, Application(), extractor=fixture, budget=DailyBudget(limit_usd=0.0))
