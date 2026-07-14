"""Single-source-of-truth discipline, made executable.

The rulebook (rules/*.yaml) is the only place TTB's statutory text and thresholds
may live. This test fails if any of them leak into source code, and it confirms
every rule carries a CFR citation. Established on day one so drift can never
start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ttb_verifier.rulebook.loader import load_rulebook

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
    # Content-addressed version: same files -> same version hash.
    assert load_rulebook().version == load_rulebook().version
