"""Brand-name matching.

The label brand and the filed brand must match the way a compliance agent means
it: "STONE'S THROW" and "Stone's Throw" are the same. That is a NORMALIZATION
problem, not a fuzzy-matching one — after ``canon_brand`` the two strings are
byte-identical, and the match is exact. Fuzzy matching is only a backstop for
genuine OCR character noise, and it is deliberately narrow:

``token_set_ratio`` / ``partial_ratio`` / ``WRatio`` are NOT used. They score
"OLD CROW" against "OLD CROW RESERVE" at 100 — a false accept of two different
TTB products, which is the dangerous direction for a compliance tool. Plain
``ratio`` (normalized Indel) does not have that failure.

Containment is not a match, but it is not a hard fail either. Real approved
filings in the COLA corpus abbreviate or extend the filed name on the art
("MORTALIS" painted for "MORTALIS BREWING COMPANY" filed; "VODKA 7" for "7"),
so when one normalized name's tokens are a strict subset of the other's the
finding routes to review — close but obviously related is a person's judgment
call. That containment is an explicit token check, never a fuzzy subset score:
the ratios above would silently auto-accept; the token check abstains.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from rapidfuzz import fuzz

from label_assay.text.normalize import canon_brand

# Below this, a post-normalization difference is treated as a real mismatch;
# at or above it (but not exact) a human decides. Set conservatively — favouring
# review over a wrong auto-verdict — and calibrated against the eval set later.
_REVIEW_FLOOR = 82.0


class BrandVerdict(enum.StrEnum):
    MATCH = "match"        # equal after normalization
    REVIEW = "review"      # close but not certain — a human decides
    MISMATCH = "mismatch"  # different brands


@dataclass(frozen=True)
class BrandFinding:
    verdict: BrandVerdict
    score: float
    detail: str


def _one_contains_the_other(a: str, b: str) -> bool:
    """True when every token of one name appears among the other's tokens and
    the other has more — a strict prefix, suffix, or subset after
    normalization ("mortalis" in "mortalis brewing"; "7" in "vodka 7").
    Deterministic and explainable, unlike a subset ratio."""
    ta, tb = set(a.split()), set(b.split())
    return bool(ta) and bool(tb) and (ta < tb or tb < ta)


def match_brand(label_brand: str | None, application_brand: str | None) -> BrandFinding:
    if not label_brand or not application_brand:
        return BrandFinding(
            BrandVerdict.REVIEW, 0.0, "Brand name missing on the label or the application."
        )

    a, b = canon_brand(label_brand), canon_brand(application_brand)
    if a and a == b:
        return BrandFinding(BrandVerdict.MATCH, 100.0, "Brand names match after normalization.")

    score = fuzz.ratio(a, b)
    if _one_contains_the_other(a, b):
        return BrandFinding(
            BrandVerdict.REVIEW, score,
            f"One brand name contains the other after normalization ({a!r} vs {b!r}): "
            "likely the same brand with surrounding words added or dropped, so a person decides.",
        )
    if score >= _REVIEW_FLOOR:
        return BrandFinding(
            BrandVerdict.REVIEW, score,
            f"Brand names are close but not identical after normalization ({a!r} vs {b!r}).",
        )
    return BrandFinding(BrandVerdict.MISMATCH, score, f"Brand names differ ({a!r} vs {b!r}).")
