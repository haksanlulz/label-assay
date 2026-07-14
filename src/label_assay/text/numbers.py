"""Parse and validate label numerics — alcohol content.

Values a regulator reads are compared in Decimal, not float, so "45 x 2 == 90"
is exact and there is no rounding to defend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# "45% Alc./Vol.", "13.5% ALC BY VOL", "5.5% alc/vol"
_ABV_PCT = re.compile(r"(?P<abv>\d{1,2}(?:\.\d+)?)\s*%")
# "ALCOHOL 40 PERCENT BY VOLUME" (no percent sign)
_ABV_WORD = re.compile(r"alc(?:ohol)?\.?\s*(?P<abv>\d{1,2}(?:\.\d+)?)\s*percent", re.IGNORECASE)
_PROOF = re.compile(r"(?P<proof>\d{1,3}(?:\.\d+)?)\s*proof", re.IGNORECASE)


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


def parse_alcohol_content(text: str | None) -> AlcoholContent | None:
    """Extract ABV (and proof, if present) from a label's alcohol-content string.
    Returns None when no alcohol content is stated or the value is impossible."""
    if not text:
        return None
    m = _ABV_PCT.search(text) or _ABV_WORD.search(text)
    if not m:
        return None
    try:
        abv = Decimal(m.group("abv"))
    except InvalidOperation:
        return None

    proof: Decimal | None = None
    pm = _PROOF.search(text)
    if pm:
        try:
            proof = Decimal(pm.group("proof"))
        except InvalidOperation:
            proof = None

    try:
        return AlcoholContent(abv=abv, proof=proof)
    except ValueError:
        return None
