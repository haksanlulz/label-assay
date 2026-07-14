"""Government Warning comparison — 27 CFR 16.21 and 16.22(a)(2).

Canonicalize (case-preserving), then compare in two views over the same string:

- verbatim: exact equality of the canonical strings.
- substance: equality after casefold.

The 2x2 is the finding. Both pass -> compliant. Substance passes but verbatim
fails -> a capitalization violation (e.g. "Government Warning" in title case,
which 16.22(a)(2) forbids). Substance fails -> a word is changed, missing, or
added. The word-level diff is for explaining the finding to a reviewer, never
for deciding it.

A VLM must never adjudicate this: vision models recite this famous paragraph
from memory and will "read" the mandated text off a label that does not carry
it. The comparison is deterministic on OCR output for that reason.
"""

from __future__ import annotations

import difflib
import enum
from dataclasses import dataclass

from label_assay.text.normalize import canon_statutory


class WarningVerdict(enum.StrEnum):
    MATCH = "match"
    CAPITALIZATION = "capitalization"  # right words, wrong case on the mandated caps
    ALTERED = "altered"                # a word changed, missing, or added
    ABSENT = "absent"                  # no warning text found


@dataclass(frozen=True)
class WarningFinding:
    verdict: WarningVerdict
    detail: str
    diff: tuple[tuple[str, str, str], ...] = ()  # (op, expected_span, found_span)


def compare_warning(found_text: str | None, reference: str) -> WarningFinding:
    if not found_text or not found_text.strip():
        return WarningFinding(WarningVerdict.ABSENT, "No Government Warning text was found.")

    ref = canon_statutory(reference)
    got = canon_statutory(found_text)

    if got == ref:
        return WarningFinding(WarningVerdict.MATCH, "Matches the mandated warning verbatim.")
    if got.casefold() == ref.casefold():
        return WarningFinding(
            WarningVerdict.CAPITALIZATION,
            "Wording is correct, but capitalization differs: the first two words of the "
            "warning must be in capital letters (27 CFR 16.22(a)(2)).",
            _word_diff(ref, got),
        )
    return WarningFinding(
        WarningVerdict.ALTERED,
        "Warning text differs from the mandated statement — a word is changed, missing, or added.",
        _word_diff(ref, got),
    )


def _word_diff(expected: str, found: str) -> tuple[tuple[str, str, str], ...]:
    """Word-level diff. autojunk is off: the warning is 283 chars, over difflib's
    200-char popularity heuristic, which would otherwise change the result."""
    exp, fnd = expected.split(), found.split()
    matcher = difflib.SequenceMatcher(a=exp, b=fnd, autojunk=False)
    out: list[tuple[str, str, str]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        out.append((op, " ".join(exp[i1:i2]), " ".join(fnd[j1:j2])))
    return tuple(out)
