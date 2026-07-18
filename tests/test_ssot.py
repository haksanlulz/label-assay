"""Single-source-of-truth discipline, made executable.

The rulebook (rules/*.yaml) is the only place TTB's statutory text and thresholds
may live. This test fails if any of them leak into source code, and it confirms
every rule carries a CFR citation. Established on day one so drift can never
start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from label_assay.rulebook.loader import load_rulebook

SRC = Path(__file__).resolve().parents[1] / "src"

# Statutory text that must appear only in the rulebook YAML, never in code.
FORBIDDEN_IN_CODE = [
    "GOVERNMENT WARNING",
    "According to the Surgeon General",
    "impairs your ability to drive",
]


@pytest.mark.parametrize("needle", FORBIDDEN_IN_CODE)
def test_no_statutory_text_hardcoded(needle: str) -> None:
    offenders = [
        str(py.relative_to(SRC))
        for py in SRC.rglob("*.py")
        if needle in py.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{needle!r} is hardcoded in {offenders}; statutory text belongs in the rulebook."
    )


def test_every_rule_is_cited() -> None:
    rulebook = load_rulebook()
    assert rulebook.rules, "rulebook loaded no rules"
    for rule in rulebook.rules:
        assert rule.citation.startswith("27 CFR"), f"rule {rule.id!r} lacks a 27 CFR citation"


def test_loader_rejects_a_rule_without_a_title() -> None:
    # The title is the plain-language name a finding is displayed under, so it
    # is as mandatory as the citation: absent or empty, the rule cannot load.
    from pydantic import ValidationError

    from label_assay.rulebook.loader import Rule

    raw = {
        "id": "untitled_rule",
        "citation": "27 CFR 5.64",
        "beverage_classes": ["all"],
        "description": "A rule with no plain-language name.",
        "match": {"strategy": "brand_match", "field": "brand_name"},
    }
    with pytest.raises(ValidationError):
        Rule(**raw)
    with pytest.raises(ValidationError):
        Rule(**raw, title="")


# AP headline case: articles, short conjunctions, and short prepositions stay
# lowercase unless first, last, or right after a colon; everything else —
# including short verbs like "Is" — is capitalized.
_AP_MINOR_WORDS = frozenset(
    "a an the and but or for nor at by in of off on per to up as via".split()
)


def _is_ap_headline_case(title: str) -> bool:
    words = title.split()
    for i, word in enumerate(words):
        bare = word.strip(":;,.—\"'")
        if not bare:
            return False
        exempt = i in (0, len(words) - 1) or words[i - 1].endswith(":")
        if bare.lower() in _AP_MINOR_WORDS and not exempt:
            if not bare[0].islower():
                return False
        elif not bare[0].isupper():
            return False
    return True


def test_every_shipped_rule_has_a_short_ap_cased_title() -> None:
    for rule in load_rulebook().rules:
        assert rule.title.strip(), f"rule {rule.id!r} has an empty title"
        assert _is_ap_headline_case(rule.title), (
            f"rule {rule.id!r} title {rule.title!r} is not AP headline case"
        )


def test_rule_titles_carry_no_statutory_text() -> None:
    # A title names the requirement; the 16.21 text itself lives only in the
    # rule's match reference. The title-case words "Government Warning" are the
    # statement's name, not the statute's all-caps rendering, which stays out
    # along with any fragment of the statement's body.
    for rule in load_rulebook().rules:
        assert "GOVERNMENT WARNING" not in rule.title
        folded = rule.title.casefold()
        for phrase in (
            "according to the surgeon general",
            "birth defects",
            "impairs your ability",
            "operate machinery",
            "health problems",
        ):
            assert phrase not in folded, f"rule {rule.id!r} title carries statutory text"


def test_rulebook_version_is_stable_across_loads() -> None:
    # Content-addressed version: same files -> same version hash. The cache is
    # cleared between loads — comparing the lru_cached object to itself would
    # pass even if the version were random per construction.
    first = load_rulebook()
    version, rule_count = first.version, len(first.rules)
    load_rulebook.cache_clear()
    try:
        second = load_rulebook()
        assert second.version == version
        assert len(second.rules) == rule_count
    finally:
        load_rulebook.cache_clear()  # leave no half-warm state for other tests


def test_every_known_strategy_has_a_matcher() -> None:
    # A strategy the loader accepts but the engine cannot dispatch would load
    # validly and then silently never run — no finding, not even NOT_EVALUABLE.
    from label_assay.rulebook.loader import KNOWN_STRATEGIES
    from label_assay.verify import engine

    assert set(KNOWN_STRATEGIES) == set(engine._MATCHERS)
