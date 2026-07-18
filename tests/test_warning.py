"""Government Warning comparator.

The test names cite the mechanic on purpose — the suite documents what the reg
requires (27 CFR 16.21 verbatim text; 16.22(a)(2) capitals) and what counts as a
violation. Cases are built by mutating the mandated text, which is the only test
that would catch a model reciting the warning from memory.
"""

from __future__ import annotations

from fixture_corpus import mandated_warning
from label_assay.match.warning import WarningVerdict, compare_warning

# Verbatim local OCR reads of two TTB-approved labels in tests/fixtures/cola
# (cola_24106001000404, cola_25150001000637): every word is printed correctly
# on the labels, but the OCR of the tiny print drops most inter-word spaces.
_SPACE_JOINED_OCR = (
    "GOVERNMENTWARNING:(1)Accordingto theSurgeonGeneral, womenshouldnot "
    "drinkalcoholicbeveragesduringpregnancy becauseoftheriskofbirth defects.(2) "
    "Consumptionofalcoholicbeveragesimpairs yourabilitytodriveacaroroperate "
    "machinery,and maycausehealthproblems."
)
_SPACE_JOINED_ALL_CAPS_OCR = (
    "GOVERNMENT WARNING:(1) ACCORDINGTOTHESURGEON GENERAL,WOMENSHOULDNOT "
    "DRINKALCOHOLICBEVERAGES DURINGPREGNANCY BECAUSEOFTHERISK OF BIRTH "
    "DEFECTS.(2) CONSUMPTIONOFALCOHOLIC BEVERAGESIMPAIRSYOURABILITY "
    "TODRIVEACAROROPERATE MACHINERY,AND MAYCAUSE HEALTH PROBLEMS."
)


def test_exact_text_matches() -> None:
    ref = mandated_warning()
    assert compare_warning(ref, ref).verdict == WarningVerdict.MATCH


def test_ocr_whitespace_and_linebreaks_still_match() -> None:
    ref = mandated_warning()
    noisy = ref.replace(" ", "  ").replace("machinery,", "machin-\nery,")
    assert compare_warning(noisy, ref).verdict == WarningVerdict.MATCH


def test_title_case_government_warning_is_a_capitalization_violation() -> None:
    # 27 CFR 16.22(a)(2): the words "GOVERNMENT WARNING" must be in capital letters.
    ref = mandated_warning()
    titlecased = ref.replace("GOVERNMENT WARNING", "Government Warning")
    assert compare_warning(titlecased, ref).verdict == WarningVerdict.CAPITALIZATION


def test_all_caps_statement_matches() -> None:
    # 16.22(a)(2) regulates the case of the heading words ONLY; TTB-approved
    # labels routinely set the entire statement in capitals, and those are legal.
    ref = mandated_warning()
    assert compare_warning(ref.upper(), ref).verdict == WarningVerdict.MATCH


def test_ocr_joined_heading_words_still_match() -> None:
    # OCR often drops the space between the rendered heading words.
    ref = mandated_warning()
    joined = ref.replace("GOVERNMENT WARNING:", "GOVERNMENTWARNING:")
    assert compare_warning(joined, ref).verdict == WarningVerdict.MATCH


def test_real_space_joined_ocr_read_matches() -> None:
    # Correct wording must not fail because print kerning and OCR dropped the
    # spaces: the body is judged on its letters and digits, not its tokenization.
    assert compare_warning(_SPACE_JOINED_OCR, mandated_warning()).verdict == WarningVerdict.MATCH


def test_real_space_joined_all_caps_ocr_read_matches() -> None:
    # Same defense on the all-caps variant: a fully capitalized body is legal,
    # and the space drops on top of it must not turn MATCH into ALTERED.
    result = compare_warning(_SPACE_JOINED_ALL_CAPS_OCR, mandated_warning())
    assert result.verdict == WarningVerdict.MATCH


def test_letter_level_change_is_still_altered_when_space_joined() -> None:
    # Space-insensitivity must not become letter-insensitivity: a real wording
    # change inside a space-joined read still fails.
    mangled = _SPACE_JOINED_OCR.replace("birth defects.", "birtheffects.")
    result = compare_warning(mangled, mandated_warning())
    assert result.verdict == WarningVerdict.ALTERED
    assert result.diff  # best-effort word diff still comes along for the reviewer


def test_misspelled_goverment_heading_is_altered() -> None:
    # The real Alsina & Sarda label (cola_24100001000120) prints "GOVERMENT
    # WARNING:" — a letter missing from the mandated heading. The heading
    # locator walks the mandated characters and refuses at the first mismatch,
    # and that code path reports ALTERED: the heading words themselves are
    # wrong. ABSENT is reserved for no warning text at all, and text was found
    # here — it is just not the mandated heading. A wording fail on this label
    # is the corpus's one true positive and must survive the space-insensitive
    # body comparison.
    ref = mandated_warning()
    misspelled = ref.replace("GOVERNMENT WARNING:", "GOVERMENT WARNING:")
    result = compare_warning(misspelled, ref)
    assert result.verdict == WarningVerdict.ALTERED
    assert result.diff


def test_punctuation_only_deviation_is_not_flagged() -> None:
    # Deliberate trade, documented in the module docstring: the body comparison
    # runs on letters and digits alone, so a period-for-comma slip is invisible
    # to it. Letter-substance over punctuation.
    ref = mandated_warning()
    repunctuated = ref.replace("machinery, and", "machinery. and")
    assert compare_warning(repunctuated, ref).verdict == WarningVerdict.MATCH


def test_changed_word_with_correct_heading_is_altered() -> None:
    ref = mandated_warning()
    swapped = ref.replace("birth defects", "birth effects")
    assert compare_warning(swapped, ref).verdict == WarningVerdict.ALTERED


def test_title_case_heading_with_changed_word_is_altered_not_capitalization() -> None:
    # ALTERED takes precedence when both violations apply.
    ref = mandated_warning()
    both = ref.replace("GOVERNMENT WARNING", "Government Warning").replace(
        "birth defects", "birth effects"
    )
    assert compare_warning(both, ref).verdict == WarningVerdict.ALTERED


def test_missing_word_is_altered_and_reports_a_diff() -> None:
    ref = mandated_warning()
    dropped = ref.replace("birth defects", "defects")  # "birth" removed
    result = compare_warning(dropped, ref)
    assert result.verdict == WarningVerdict.ALTERED
    assert result.diff  # non-empty, so a reviewer can see what changed


def test_changed_word_is_altered() -> None:
    ref = mandated_warning()
    changed = ref.replace("should not drink", "should not consume")
    assert compare_warning(changed, ref).verdict == WarningVerdict.ALTERED


def test_absent_when_no_text() -> None:
    ref = mandated_warning()
    assert compare_warning("", ref).verdict == WarningVerdict.ABSENT
    assert compare_warning(None, ref).verdict == WarningVerdict.ABSENT
