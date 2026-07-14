"""Government Warning comparator.

The test names cite the mechanic on purpose — the suite documents what the reg
requires (27 CFR 16.21 verbatim text; 16.22(a)(2) capitals) and what counts as a
violation. Cases are built by mutating the mandated text, which is the only test
that would catch a model reciting the warning from memory.
"""

from __future__ import annotations

from label_assay.match.warning import WarningVerdict, compare_warning
from label_assay.rulebook.loader import load_rulebook


def _ref() -> str:
    rb = load_rulebook()
    return next(r for r in rb.rules if r.id == "health_warning_verbatim").match.reference


def test_exact_text_matches() -> None:
    ref = _ref()
    assert compare_warning(ref, ref).verdict == WarningVerdict.MATCH


def test_ocr_whitespace_and_linebreaks_still_match() -> None:
    ref = _ref()
    noisy = ref.replace(" ", "  ").replace("machinery,", "machin-\nery,")
    assert compare_warning(noisy, ref).verdict == WarningVerdict.MATCH


def test_title_case_government_warning_is_a_capitalization_violation() -> None:
    # 27 CFR 16.22(a)(2): the words "GOVERNMENT WARNING" must be in capital letters.
    ref = _ref()
    titlecased = ref.replace("GOVERNMENT WARNING", "Government Warning")
    assert compare_warning(titlecased, ref).verdict == WarningVerdict.CAPITALIZATION


def test_missing_word_is_altered_and_reports_a_diff() -> None:
    ref = _ref()
    dropped = ref.replace("birth defects", "defects")  # "birth" removed
    result = compare_warning(dropped, ref)
    assert result.verdict == WarningVerdict.ALTERED
    assert result.diff  # non-empty, so a reviewer can see what changed


def test_changed_word_is_altered() -> None:
    ref = _ref()
    changed = ref.replace("should not drink", "should not consume")
    assert compare_warning(changed, ref).verdict == WarningVerdict.ALTERED


def test_absent_when_no_text() -> None:
    ref = _ref()
    assert compare_warning("", ref).verdict == WarningVerdict.ABSENT
    assert compare_warning(None, ref).verdict == WarningVerdict.ABSENT
