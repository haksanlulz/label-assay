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


def parse_alcohol_content(text: str | None) -> AlcoholContent | None:
    """Extract ABV (and proof, if present) from a label's alcohol-content string.
    Returns None when no alcohol content is stated, values conflict, or a proof
    claim cannot be parsed. Repeating an equivalent value is not a conflict.
    This checks the supplied field, not statements omitted by the readers."""
    if not text:
        return None
    explicit = list(_ABV_EXPLICIT_PCT.finditer(text))
    words = list(_ABV_WORD.finditer(text))
    if (len(explicit) != len(_ABV_EXPLICIT_MARKER.findall(text)) or
            len(words) != len(_ABV_WORD_MARKER.findall(text))):
        return None  # e.g. "4O% ALC/VOL" must not disappear beside a valid claim
    matches = [*_ABV_PCT.finditer(text), *explicit, *words]
    if not matches:
        return None
    try:
        abvs = {Decimal(match.group("abv")) for match in matches}
    except InvalidOperation:
        return None
    if len(abvs) != 1:
        return None
    abv = abvs.pop()

    proof: Decimal | None = None
    proof_matches = list(_PROOF.finditer(text))
    if len(proof_matches) != len(_PROOF_MARKER.findall(text)):
        # A present but unreadable proof is not the same as no proof stated.
        return None
    if proof_matches:
        try:
            proofs = {Decimal(match.group("proof")) for match in proof_matches}
        except InvalidOperation:
            return None
        if len(proofs) != 1:
            return None
        proof = proofs.pop()
        if not Decimal(0) <= proof <= Decimal(200):
            return None

    try:
        return AlcoholContent(abv=abv, proof=proof)
    except ValueError:
        return None
