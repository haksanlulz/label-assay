# 2. The statutory warning is compared deterministically, never by the model

Date: 2026-07-14

## Status

Accepted

## Context

The health warning statement (27 CFR 16.21) is fixed by statute — 283 characters, exact. Checking it looks like a natural language task, so the tempting implementation is to ask the model whether the warning is correct.

That is the single most dangerous thing this system could do. The mandated warning is one of the most reproduced paragraphs in American commercial text. A vision model does not read it; it recognizes it and completes it from memory. Shown a label whose warning is missing a word or whose heading is title-cased, the model reports the correct statutory text — because that is what it "knows" the paragraph says. The tool would then pass exactly the labels it exists to catch, and do so confidently.

## Decision

The model transcribes the warning as printed. The comparison happens in code: canonicalize, then compare exactly.

Canonicalization is admissible only if it is a provable no-op on the reference text — a rule enforced by test. Whitespace collapse, line-break de-hyphenation, and quote folding pass. `casefold` does not, because it would rewrite the mandated capitals and destroy the capitalization check.

Two views run over the same canonical string: exact, and casefolded. Both pass means compliant. Substance passing while exact fails is precisely a capitalization violation. Substance failing means a word changed, was dropped, or was added.

## Consequences

The check is exact, fast, and explainable — and the 2×2 above falls out as the finding taxonomy for free.

Diffs are for explaining a finding to a reviewer, never for deciding it. Homoglyphs are diagnosed rather than repaired: folding them would silently mask a real substitution.
