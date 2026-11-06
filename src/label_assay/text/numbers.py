"""Parse and validate label numerics — alcohol content.

Values a regulator reads are compared in Decimal, not float, so "45 x 2 == 90"
is exact and there is no rounding to defend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# "45% Alc./Vol.", "13.5% ALC BY VOL", "5.5% alc/vol". The lookbehind keeps a
# longer digit run from matching by truncation: without it, the "00%" inside
# "TEQUILA 100% AGAVE" parses as ABV 0 and the real "40%" is never reached.
_ABV_PCT = re.compile(r"(?<![\d.])(?P<abv>[+-]?\d{1,2}(?:\.\d+)?)\s*%")
# Longer percentages are normally marketing ("100% DE AGAVE"), but an explicit
# alcohol unit makes even an out-of-range number a claim we must not discard.
_ABV_EXPLICIT_PCT = re.compile(
    r"(?<![\d.])(?P<abv>[+-]?\d+(?:\.\d+)?)\s*%\s*(?:alc(?:ohol)?\b|abv\b)",
    re.IGNORECASE,
)
_ABV_EXPLICIT_MARKER = re.compile(r"%\s*(?:alc(?:ohol)?\b|abv\b)", re.IGNORECASE)
# "ALCOHOL 40 PERCENT BY VOLUME" (no percent sign)
_ABV_WORD = re.compile(r"alc(?:ohol)?\.?\s*(?P<abv>[+-]?\d+(?:\.\d+)?)\s*percent", re.IGNORECASE)
_ABV_WORD_MARKER = re.compile(r"alc(?:ohol)?\.?\s*\S+?\s*percent\b", re.IGNORECASE)
_PROOF = re.compile(r"(?<![\d.+-])(?P<proof>\d{1,3}(?:\.\d+)?)\s*proof\b", re.IGNORECASE)
# Permit joined OCR such as "80PROOF", but not ordinary words such as waterproof.
_PROOF_MARKER = re.compile(r"(?<![a-z])proof(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True)
class AlcoholContent:
    abv: Decimal
    proof: Decimal | None = None

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.abv <= Decimal(100)):
            raise ValueError(f"ABV out of range: {self.abv}")

    @property
    def proof_matches_abv(self) -> bool | None:
        """US proof is defined as twice the ABV (27 CFR 5.1). When a proof is
        stated it must be internally consistent. None = no proof to check."""
        if self.proof is None:
            return None
        return self.abv * 2 == self.proof


@dataclass(frozen=True)
class AlcoholReading:
    """What the parser made of an alcohol-content string: the content, or the
    reason there is none. ``reason`` is None only when nothing was stated, so a
    reviewer can tell "the label says nothing" from "the label says two things"."""
    content: AlcoholContent | None
    reason: str | None = None


def _listed(values: set[Decimal], unit: str) -> str:
    return " and ".join(f"{value}{unit}" for value in sorted(values))


def read_alcohol_content(text: str | None) -> AlcoholReading:
    """Extract ABV (and proof, if present) from a label's alcohol-content string.
    The content is None when no alcohol content is stated, values conflict, or a
    claim cannot be parsed; every case but "nothing stated" names its reason.
    Repeating an equivalent value is not a conflict. This checks the supplied
    field, not statements omitted by the readers."""
    unreadable_abv = "an alcohol content claim could not be read as a number"
    unreadable_proof = "a proof claim could not be read as a number"
    if not text:
        return AlcoholReading(None)
    explicit = list(_ABV_EXPLICIT_PCT.finditer(text))
    words = list(_ABV_WORD.finditer(text))
    if (len(explicit) != len(_ABV_EXPLICIT_MARKER.findall(text)) or
            len(words) != len(_ABV_WORD_MARKER.findall(text))):
        # e.g. "4O% ALC/VOL" must not disappear beside a valid claim
        return AlcoholReading(None, unreadable_abv)
    matches = [*_ABV_PCT.finditer(text), *explicit, *words]
    if not matches:
        return AlcoholReading(None)
    try:
        abvs = {Decimal(match.group("abv")) for match in matches}
    except InvalidOperation:
        return AlcoholReading(None, unreadable_abv)
    if len(abvs) != 1:
        return AlcoholReading(None, f"the label states more than one alcohol by volume ({_listed(abvs, '%')})")
    abv = abvs.pop()

    proof: Decimal | None = None
    proof_matches = list(_PROOF.finditer(text))
    if len(proof_matches) != len(_PROOF_MARKER.findall(text)):
        # A present but unreadable proof is not the same as no proof stated.
        return AlcoholReading(None, unreadable_proof)
    if proof_matches:
        try:
            proofs = {Decimal(match.group("proof")) for match in proof_matches}
        except InvalidOperation:
            return AlcoholReading(None, unreadable_proof)
        if len(proofs) != 1:
            return AlcoholReading(None, f"the label states more than one proof ({_listed(proofs, ' proof')})")
        proof = proofs.pop()
        if not Decimal(0) <= proof <= Decimal(200):
            return AlcoholReading(None, f"the stated proof ({proof}) is outside 0 to 200")

    try:
        return AlcoholReading(AlcoholContent(abv=abv, proof=proof))
    except ValueError:
        return AlcoholReading(None, f"the stated alcohol by volume ({abv}%) is outside 0 to 100")


def parse_alcohol_content(text: str | None) -> AlcoholContent | None:
    """The content alone; ``read_alcohol_content`` also says why it is None."""
    return read_alcohol_content(text).content
