"""Text canonicalization.

Three transforms, because the domain needs different things from them:

- ``canon_statutory`` — for the Government Warning. Collapses benign OCR noise
  (whitespace runs, line-break hyphenation, curly quotes, width variants) but
  PRESERVES CASE, so the check on the mandated capitals still has something to
  look at.
- ``canon_brand`` — for brand names. Aggressive: repairs mojibake, folds
  diacritics, strips legal suffixes, casefolds — so "STONE'S THROW" and
  "Stone's Throw" collapse to the same string and the match is exact, not fuzzy.
- ``squash`` — the flattest form: casefolded alphanumerics, nothing else. For
  comparisons that must survive OCR dropping the spaces between rendered words
  (corroboration in verify/confidence.py, the warning-body wording check in
  match/warning.py).

Admissibility rule (enforced in tests): every step of ``canon_statutory`` is a
provable no-op on the verbatim 27 CFR 16.21 reference. casefold is therefore
inadmissible in that path — it would rewrite the mandated capitals and destroy
the capitalization check.
"""

from __future__ import annotations

import re
import unicodedata

from anyascii import anyascii
from ftfy import fix_text

# Every apostrophe/quote variant -> its ASCII form (case-irrelevant).
_QUOTES = {
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x02BC: "'", 0xFF07: "'", 0x2032: "'",
    0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"', 0x2033: '"',
}
# Zero-width characters and the soft hyphen: delete outright.
_ZERO_WIDTH = dict.fromkeys((0x200B, 0x200C, 0x200D, 0xFEFF, 0x00AD), None)
# Dash variants -> ASCII hyphen-minus.
_DASHES = {c: "-" for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)}
# The straight apostrophe, deleted in brand names ("STONE'S" -> "STONES").
_APOSTROPHE = dict.fromkeys((0x27,), None)

_WS = re.compile(r"\s+")
_ALNUM = re.compile(r"[^a-z0-9]")
_HYPHEN_LINEBREAK = re.compile(r"-\s*\n\s*")  # "de-\nfects" -> "defects"
_NON_WORD = re.compile(r"[^\w\s]")
_ARTICLES = frozenset({"the", "a", "an"})
# US legal/company suffixes stripped from a brand name's tail. Curated on purpose
# (not a general company-name library) so behaviour is deterministic and every
# entry is defensible, and so international forms like the Swedish "AB" don't eat
# a real brand word.
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "ltd", "limited", "co", "corp",
    "corporation", "company", "lp", "llp", "plc",
})


def _base(s: str) -> str:
    """Shared, case-preserving canonicalization. NFKC runs first, because it can
    emit characters (e.g. U+0149 -> a modifier apostrophe) that the translate
    tables then fold to ASCII; running NFKC last would leave those for a second
    pass and break idempotence."""
    s = _HYPHEN_LINEBREAK.sub("", s)
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_QUOTES)
    s = s.translate(_ZERO_WIDTH)
    s = s.translate(_DASHES)
    return _WS.sub(" ", s).strip()


def canon_statutory(s: str) -> str:
    """Case-preserving canonical form for verbatim statutory-text comparison."""
    return _base(s)


def squash(s: str) -> str:
    """Space-, punctuation-, and case-insensitive form: the casefolded
    alphanumerics and nothing else. OCR of small print routinely drops the
    spaces between rendered words ("OLDTOMDISTILLERY"), so checks that must
    survive that read compare this collapsed form."""
    return _ALNUM.sub("", s.casefold())


def canon_brand(s: str) -> str:
    """Aggressive canonical form for brand-name equality."""
    s = fix_text(s)             # mojibake: "STONEâ€™S" -> "STONE'S"
    s = _base(s)                # quotes/width/dashes/whitespace, case preserved
    s = anyascii(s)             # fold diacritics: "RÉMY" -> "REMY", "Œuf" -> "Oeuf"
    s = s.casefold()
    s = s.replace("&", " and ")   # before punctuation strip, so "M&S" -> "m and s"
    s = s.translate(_APOSTROPHE)  # "stone's" -> "stones" (delete, do not split)
    s = _NON_WORD.sub(" ", s)     # any remaining punctuation -> space
    tokens = [t for t in s.split() if t not in _ARTICLES]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:  # "... distillery inc" -> "... distillery"
        tokens.pop()
    return " ".join(tokens)
