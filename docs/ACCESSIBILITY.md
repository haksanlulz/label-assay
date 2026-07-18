# Accessibility

Target: WCAG 2.1 AA, to be Section 508-ready. This is a prototype and has not been formally audited or tested with assistive technology.

**Built in**

- The single-label flow is server-rendered and works with JavaScript disabled; no script is required. With scripting on, the upload forms add a submit guard that disables the button during the check (the busy wording remains the button's accessible name) and reveals a `role="status"` progress line, announced once; with scripting off the button stays enabled and the form submits as before. The batch results page carries the other script, which polls for progress.
- Status is never carried by colour alone: every verdict has a text label and a distinct left border.
- 18px base text and 44px targets, suiting the reviewer audience (about half the team is over 50).
- Semantic HTML: labelled inputs, a real table with column headers, visible focus rings, keyboard-operable throughout.
- Live regions announce batch progress and the summary.
- Contrast meets AA against the USWDS-derived colour tokens (the needs-review ink is the USWDS gold darkened one step, because the stock value measures 4.07:1 at badge size). A test recomputes every badge pair's ratio from the stylesheet.

**Known gaps**

- No screen-reader or automated-audit pass has been run.
- The batch results page (progress, result rows, filtering) is script-rendered; with JavaScript off it explains itself via a noscript notice, and the CSV export link is the working no-JS path once the batch finishes.
- Error copy has not been reviewed for plain-language conformance.
