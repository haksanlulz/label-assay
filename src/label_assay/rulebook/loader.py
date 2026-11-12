"""Load and validate the YAML rulebook.

Validation happens on load: a rule without a CFR citation, or with an unknown
match strategy, raises at import rather than producing a silently wrong verdict
later.
"""

from __future__ import annotations

import hashlib
import importlib.resources as resources
from functools import lru_cache

from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from label_assay.extract.base import Extraction

# The closed vocabulary of match strategies the engine knows how to dispatch.
# A rule naming anything outside this set fails to load. New strategies are
# added deliberately, in code and here — never invented in a rule file. A test
# pins this set equal to the engine's matcher registry, so a strategy that
# loads but silently never runs cannot exist.
KNOWN_STRATEGIES = frozenset({"verbatim", "brand_match", "abv_consistency", "warning_bold"})


class Match(BaseModel):
    # A misspelled key ("feild", "refrence") used to be dropped without a word,
    # leaving a rule that loaded and then never applied or crashed at verify
    # time. Unknown keys now fail the load.
    model_config = ConfigDict(extra="forbid")

    strategy: str
    field: str                     # the extraction field this rule checks
    reference: str | None = None   # statutory text, for verbatim comparisons

    @model_validator(mode="after")
    def _checkable(self) -> Match:
        if self.field not in Extraction.model_fields:
            raise ValueError(
                f"match.field {self.field!r} is not a field the reader extracts; "
                f"known: {sorted(Extraction.model_fields)}"
            )
        if self.strategy == "verbatim" and not (self.reference or "").strip():
            raise ValueError("a verbatim rule needs the reference text it is compared against")
        return self


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    # Required, like the citation: the short plain-language name (AP headline
    # case) a finding is displayed under. A rule a person cannot name at a
    # glance cannot load.
    title: str = Field(min_length=1)
    citation: str = Field(min_length=1)  # required: an uncited rule cannot load
    # "all", or the classes infer_beverage_class can return. A rule listing
    # none, or a class that does not exist, would load and never run.
    beverage_classes: list[Literal["all", "spirits", "wine", "malt"]] = Field(min_length=1)
    # Every shipped rule says "fail", and nothing reads it: what a finding's
    # verdict is, the matcher decides. Declared so the key is accepted and so a
    # different value is refused instead of silently meaning nothing.
    severity: Literal["fail"] = "fail"
    description: str
    match: Match

    def applies_to(self, beverage_class: str) -> bool:
        return "all" in self.beverage_classes or beverage_class in self.beverage_classes


class Rulebook(BaseModel):
    rules: list[Rule]
    version: str

    def rules_for(self, beverage_class: str) -> list[Rule]:
        return [r for r in self.rules if r.applies_to(beverage_class)]


@lru_cache(maxsize=1)
def load_rulebook() -> Rulebook:
    rules: list[Rule] = []
    hasher = hashlib.sha256()
    rules_dir = resources.files("label_assay.rulebook") / "rules"

    for entry in sorted(rules_dir.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith((".yaml", ".yml")):
            continue
        text = entry.read_text(encoding="utf-8")
        hasher.update(text.encode("utf-8"))
        doc = yaml.safe_load(text) or {}
        for raw in doc.get("rules", []):
            rule = Rule(**raw)
            if rule.match.strategy not in KNOWN_STRATEGIES:
                raise ValueError(
                    f"rule {rule.id!r} uses unknown match strategy "
                    f"{rule.match.strategy!r}; known: {sorted(KNOWN_STRATEGIES)}"
                )
            rules.append(rule)

    if not rules:
        raise ValueError("rulebook is empty — no rules loaded from rules/*.yaml")
    require_unique_ids(rules)

    return Rulebook(rules=rules, version=hasher.hexdigest()[:12])


def require_unique_ids(rules: list[Rule]) -> None:
    """Rule ids key the CSV grid, saved reports and comparisons; two rules
    sharing one would overwrite each other there."""
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise ValueError(f"rule id {rule.id!r} appears more than once in the rulebook")
        seen.add(rule.id)
