"""The daily spend guard — the app-side bound on what a public deployment can cost."""

from __future__ import annotations

import datetime as dt
import io

import pytest
from PIL import Image

from label_assay.domain.models import Application
from label_assay.extract.fixture import FixtureExtractor
from label_assay.web.budget import EST_COST_PER_LABEL_USD, BudgetExhausted, DailyBudget
from label_assay.web.service import ExtractionUnavailable, check_label


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


def test_exhausted_budget_stops_the_reader_before_it_is_called() -> None:
    # An exhausted budget must refuse before any paid call, and surface as a
    # clean user-facing message rather than an internal error. The extractor is
    # given no fixtures at all: reaching it would KeyError, so passing proves
    # the refusal happened first.
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")

    with pytest.raises(ExtractionUnavailable, match="daily limit"):
        check_label(
            buffer.getvalue(),
            Application(),
            extractor=FixtureExtractor({}),
            budget=DailyBudget(limit_usd=0.0),
        )
