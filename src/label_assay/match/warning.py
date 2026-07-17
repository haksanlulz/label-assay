"""Government Warning comparison — 27 CFR 16.21 and 16.22(a)(2).

Canonicalize (case-preserving), then compare the statement in two parts,
because the regulation treats them differently:

- heading: the statement's first two words with their colon ("Government
  Warning" in the mandated capitals) — the only words whose case 16.22(a)(2)
  regulates. Located space-insensitively, because OCR often drops the space
  between the rendered heading words (the same concern the bold check
  handles), then compared case-sensitively.
- body: the remainder. 16.21 fixes its wording, but no regulation fixes its
  case — TTB-approved labels routinely set the entire statement in capitals —
  so it is compared casefolded.

Heading exact and body words equal -> MATCH. Heading present in the wrong
case with the body words equal -> CAPITALIZATION. Any changed, missing, or
added word -> ALTERED, which takes precedence over CAPITALIZATION. The
word-level diff is for explaining the finding to a reviewer, never for
deciding it.

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


_ALTERED_DETAIL = (
    "Warning text differs from the mandated statement — a word is changed, missing, or added."
)


def compare_warning(found_text: str | None, reference: str) -> WarningFinding:
    if not found_text or not found_text.strip():
        return WarningFinding(WarningVerdict.ABSENT, "No Government Warning text was found.")

    ref = canon_statutory(reference)
    got = canon_statutory(found_text)

    ref_words = ref.split()
    ref_heading, ref_body = " ".join(ref_words[:2]), " ".join(ref_words[2:])

    located = _locate_heading(got, ref_heading)
    if located is None:
        # The heading words themselves are altered or missing.
        return WarningFinding(WarningVerdict.ALTERED, _ALTERED_DETAIL, _word_diff(ref, got))
    got_heading, got_body = located

    if got_body.casefold() != ref_body.casefold():
        # ALTERED outranks CAPITALIZATION: wrong words are the graver finding.
        return WarningFinding(WarningVerdict.ALTERED, _ALTERED_DETAIL, _word_diff(ref, got))

    if "".join(got_heading.split()) != "".join(ref_heading.split()):
        return WarningFinding(
            WarningVerdict.CAPITALIZATION,
            f'Wording is correct, but the heading is printed as "{got_heading}": the two '
            "heading words must be in capital letters (27 CFR 16.22(a)(2)).",
            (("replace", ref_heading, got_heading),),
        )
    return WarningFinding(
        WarningVerdict.MATCH,
        "Matches the mandated warning: word-for-word, with the heading in capital letters.",
    )


def _locate_heading(text: str, heading: str) -> tuple[str, str] | None:
    """Find ``heading`` at the start of ``text``, ignoring case and internal
    whitespace. Returns (heading as printed, remainder) or None when the
    heading's characters do not open the text."""
    target = "".join(heading.split()).casefold()
    pos = 0
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        if ch.casefold() != target[pos]:
            return None
        pos += 1
        if pos == len(target):
            return text[: i + 1], text[i + 1:].strip()
    return None


def _word_diff(expected: str, found: str) -> tuple[tuple[str, str, str], ...]:
    """Word-level diff, matched casefolded (case is never the substance finding)
    but displayed as printed. autojunk is off: the warning is 283 chars, over
    difflib's 200-char popularity heuristic, which would otherwise change the
    result."""
    exp, fnd = expected.split(), found.split()
    matcher = difflib.SequenceMatcher(
        a=[w.casefold() for w in exp], b=[w.casefold() for w in fnd], autojunk=False
    )
    out: list[tuple[str, str, str]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        out.append((op, " ".join(exp[i1:i2]), " ".join(fnd[j1:j2])))
    return tuple(out)
