# Accessibility

Target: WCAG 2.1 AA, to be Section 508-ready. This is a prototype and has not been formally audited or tested with assistive technology.

**Built in**

- The single-label flow is server-rendered and works with JavaScript disabled. Script is limited to the batch results page, which polls for progress.
- Status is never carried by colour alone: every verdict has a text label and a distinct left border.
- 18px base text and 44px targets, suiting the reviewer audience (about half the team is over 50).
- Semantic HTML: labelled inputs, a real table with column headers, visible focus rings, keyboard-operable throughout.
- Live regions announce batch progress and the summary.
- Contrast meets AA against the USWDS design tokens used for colour.

**Known gaps**

- No screen-reader or automated-audit pass has been run.
- Batch filtering is script-only; with JavaScript off, the results page does not populate.
- Error copy has not been reviewed for plain-language conformance.
