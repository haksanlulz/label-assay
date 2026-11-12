"""Second round of regressions from the 2026-11-12 audit. Each test failed on
the code as it stood at 2cf4862."""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import fixture_corpus
from label_assay.domain.models import Verdict
from label_assay.rulebook.loader import Match, Rule, load_rulebook
from label_assay.verify.engine import infer_beverage_class, verify
from label_assay.web import app as webapp
from label_assay.web.budget import EST_COST_PER_LABEL_USD, DailyBudget

SPEC = fixture_corpus.known_good_compliant()
client = TestClient(webapp.app)


# --- a regulatory check that crashes is a review, not a skipped check (DECIDING-5) ---

def test_a_crash_in_the_bold_check_holds_the_label(monkeypatch: pytest.MonkeyPatch) -> None:
    import label_assay.match.bold as bold

    def boom(image, ocr_lines):
        raise RuntimeError("vision library fell over")

    monkeypatch.setattr(bold, "check_warning_bold", boom)
    report = verify(
        fixture_corpus.perfect_extraction(SPEC), fixture_corpus.application_for(SPEC), load_rulebook(),
        image=fixture_corpus.fixture_path(SPEC).read_bytes(), ocr_lines=fixture_corpus.fixture_ocr_lines(),
    )
    finding = next(f for f in report.findings if f.rule_id == "health_warning_bold")
    assert finding.verdict == Verdict.NEEDS_REVIEW
    assert report.verdict != Verdict.PASS


def test_no_image_at_all_is_still_not_evaluable() -> None:
    report = verify(fixture_corpus.perfect_extraction(SPEC), fixture_corpus.application_for(SPEC), load_rulebook())
    finding = next(f for f in report.findings if f.rule_id == "health_warning_bold")
    assert finding.verdict == Verdict.NOT_EVALUABLE


# --- beverage class from whole words (DECIDING-7) ---

@pytest.mark.parametrize(("text", "expected"), [
    ("Porter", "malt"),
    ("Imported Vodka", "spirits"),
    ("Sherry Cask Bourbon", "spirits"),
    ("Single Malt Scotch Whisky", "spirits"),
    ("Ruby Port", "wine"),
    ("Sparkling Wine/Champagne", "wine"),
    ("Malt Beverages Specialities - Flavored", "malt"),
    ("Straight Bourbon Whisky", "spirits"),
    ("Rum Specialties", "spirits"),
    ("Table Red Wine", "wine"),
    ("Apple Table Wine/Cider", "wine"),
    ("BEER", "malt"),
    ("", "spirits"),
])
def test_beverage_class_is_read_from_whole_words(text: str, expected: str) -> None:
    assert infer_beverage_class(text) == expected


# --- the rulebook loader refuses what it used to ignore (DECIDING-8) ---

def _rule(**overrides):
    raw = {"id": "r", "title": "T", "citation": "27 CFR 1", "beverage_classes": ["all"], "description": "d",
           "match": {"strategy": "verbatim", "field": "government_warning", "reference": "A B C"}}
    raw.update(overrides)
    return raw


def test_a_well_formed_rule_loads() -> None:
    assert Rule(**_rule()).applies_to("spirits")


@pytest.mark.parametrize("raw", [
    _rule(match={"strategy": "verbatim", "feild": "government_warning", "reference": "A B C"}),   # misspelled key
    _rule(match={"strategy": "verbatim", "field": "government_warning"}),                          # no reference
    _rule(match={"strategy": "verbatim", "field": "government_warning", "reference": "  "}),       # blank reference
    _rule(match={"strategy": "brand_match", "field": "brand"}),                                    # not a field
    _rule(match={"strategy": "brand_match"}),                                                      # no field
    _rule(beverage_classes=["spirit"]),                                                            # not a class
    _rule(beverage_classes=[]),                                                                    # applies to nothing
    _rule(citaton="27 CFR 1"),                                                                     # misspelled key
])
def test_a_malformed_rule_does_not_load(raw: dict) -> None:
    with pytest.raises(ValidationError):
        Rule(**raw)


def test_duplicate_rule_ids_are_refused() -> None:
    from label_assay.rulebook.loader import require_unique_ids

    with pytest.raises(ValueError, match="more than once"):
        require_unique_ids([Rule(**_rule()), Rule(**_rule())])


def test_the_shipped_rulebook_still_loads_with_its_version() -> None:
    load_rulebook.cache_clear()
    book = load_rulebook()
    assert len(book.rules) == 4
    assert book.version == "d0550ba69224"
    assert isinstance(book.rules[0].match, Match)


# --- /health tells the truth about a spent budget (SERVER-6) ---

def test_budget_reports_exhaustion_without_spending() -> None:
    budget = DailyBudget(limit_usd=EST_COST_PER_LABEL_USD)
    day = dt.date(2026, 11, 12)
    assert budget.exhausted(today=day) is False
    budget.reserve(today=day)
    assert budget.exhausted(today=day) is True
    assert budget.exhausted(today=day + dt.timedelta(days=1)) is False  # a new day


def test_health_is_degraded_when_the_daily_budget_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webapp, "_ocr_status", lambda: "ready")
    monkeypatch.setattr(webapp, "get_settings", lambda: type("S", (), {"anthropic_api_key": "k"})())
    monkeypatch.setattr(webapp, "_BUDGET", DailyBudget(limit_usd=0.0))
    payload = client.get("/health").json()
    assert payload["budget"] == "exhausted"
    assert payload["status"] == "degraded"
    monkeypatch.setattr(webapp, "_BUDGET", DailyBudget(limit_usd=5.0))
    payload = client.get("/health").json()
    assert payload["budget"] == "available"
    assert payload["status"] == "ok"


# --- an upload that stops arriving gives its slot back (SERVER-2) ---

def test_a_stalled_upload_is_timed_out_and_releases_its_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webapp, "_BODY_IDLE_SECONDS", 0.05)
    sent: list[dict] = []

    async def inner(scope, receive, send):
        await receive()  # the handler asks for the body, which never comes
        raise AssertionError("the stalled read should not have returned")

    async def stalled_receive():
        await asyncio.sleep(30)

    async def send(message):
        sent.append(message)

    ceiling = webapp._RequestSizeCeiling(inner, max_bytes=1000)
    scope = {"type": "http", "method": "POST", "path": "/check", "headers": []}

    async def scenario():
        await asyncio.wait_for(ceiling(scope, stalled_receive, send), timeout=3)

    asyncio.run(scenario())
    assert sent and sent[0]["type"] == "http.response.start" and sent[0]["status"] == 408
    # All four slots are free again: the stalled request did not keep one.
    taken = [webapp._OTHER_UPLOAD_SLOTS.acquire(blocking=False) for _ in range(4)]
    for ok in taken:
        if ok:
            webapp._OTHER_UPLOAD_SLOTS.release()
    assert all(taken)


# --- label content is not left in caches (SERVER-10) ---

def test_pages_carrying_label_data_are_not_cacheable_and_assets_are() -> None:
    from label_assay.web.batch import create_job

    job = create_job(["a.png"])
    for path in ("/", f"/batch/{job.id}", f"/batch/{job.id}/data", "/health"):
        assert client.get(path).headers.get("cache-control") == "no-store", path
    assert client.get("/static/app.css").headers.get("cache-control") != "no-store"


# --- each page has its own title (FRONTEND-14) ---

def test_pages_do_not_share_one_title() -> None:
    import re

    def title(path: str) -> str:
        return re.search(r"<title>(.*?)</title>", client.get(path).text, re.S).group(1)

    titles = {path: title(path) for path in ("/", "/batch", "/compare", "/batch/nope")}
    assert len(set(titles.values())) == len(titles), titles
    assert all(t.endswith("LabelAssay") and t != "LabelAssay" for t in titles.values()), titles


# --- a reply cut off mid-extraction is named as such (READER-12) ---

@pytest.mark.parametrize("reason", ["max_tokens", "refusal"])
def test_a_cut_off_model_reply_is_reported_plainly(reason: str) -> None:
    from types import SimpleNamespace

    from label_assay.extract.haiku import HaikuExtractor

    extractor = HaikuExtractor(api_key="not-a-real-key")
    half = SimpleNamespace(type="tool_use", input={"brand_name": {"verbatim": "X", "found": True, "value": "X"}})
    extractor._client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **_: SimpleNamespace(stop_reason=reason, content=[half])))
    png = fixture_corpus.fixture_path(SPEC).read_bytes()
    with pytest.raises(RuntimeError, match=reason):
        extractor.extract(png)
