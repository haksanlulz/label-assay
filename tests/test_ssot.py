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
