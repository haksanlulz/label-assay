# Changelog

## 0.2.0 — prepared locally, 2026-11-04

- Review queue separates corrections, review findings, uncheckable checks and unreadable files.
- Completed reports retain check times, full findings, citations and text differences.
- Printable single-label and batch reports; JSON exports support comparisons between saved revisions.
- Individual results export comparison-ready JSON; standalone HTML reports preserve styling without scripts or uploaded-image previews.
- Revision comparisons distinguish resolved findings from removed/uncheckable rules and warn about rulebook and coverage changes.
- Includes preceding local extraction, warning and batch-upload correctness fixes.
- Mobile batch results become readable cards while preserving filters; revision metadata stacks on narrow screens and file inputs stay within the form. Printed headers use high-contrast text.
- Keyboard skip links, announced review-filter counts, reduced-motion progress and focused upload errors. An invalid earlier report remains identified when the corrected report is valid. Compact print spacing prevents a footer-only page; the first batch label can share its introduction page.
- `tests/browser_server.py` supplies synthetic completed reports to the local cross-browser regression suite without contacting an AI reader.

Rulebook data is unchanged. This candidate has not been deployed or published.
