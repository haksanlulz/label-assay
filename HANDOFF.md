### 2026-11-05 — Preserve review provenance and evaluation failure evidence (gpt-6.1-terra)

Version 2 review records bind the declared app version (or explicit unknown) into the source digest; existing version 1 records retain their original schema and digest validation. Comparisons flag blank rulebook identifiers as unknown; missing identifiers remain invalid imports. Unexpected offline evaluation exceptions replace prior passing artifacts with refusal evidence when the output directory is writable. Added one independent boldness-abstention evidence expectation; evidence assertions remain selective.

# LabelAssay handoff

## Last Updated

### 2026-11-05 — Evidence baseline and comparison provenance (gpt-6-sol)

The offline gate now checks reviewer-facing rule titles and citations against the pinned rulebook, and checks an independent expected detail/text-difference record for one evidence-bearing finding in each scenario. Deliberate citation, title, detail and diff corruption must fail the gate while verdicts stay unchanged. The baseline still does not measure vision accuracy, all report prose, model consistency or legal approval.

Saved-report comparison now carries declared app versions through import. It flags differing labels and missing/older version data separately, and calls a fail-to-pass transition “Now passes in this check” without attributing the cause to corrected artwork. Matching declared versions do not authenticate a build or establish identical OCR/model behavior. The rulebook and verifier logic remain unchanged; no push or deployment.

### 2026-11-04 — Offline evaluation and report-linked human review (gpt-6-astra)

Goal: strengthen reproducible engineering evidence and reviewer handoff without changing the regulatory rulebook. Added a pinned 12-scenario / 48-decision engine baseline, expected/actual matrices, explicit abstention counts, full JSON artifacts and a nondeploying pull-request workflow definition. Added `/review`: upload a completed saved-report row, record per-finding decisions/notes and export a versioned human record with the unchanged normalized report and its SHA-256 digest. Pending decisions remain incomplete; neither completion nor the self-entered reviewer name implies approval/authentication. No model call or persisted review history.

Owners: `evaluation.py` + `tests/fixtures/evaluation/cases.json`; `domain/review.py` owns review schema/hash and `web/review.py` owns stateless routes. Existing engine and rulebook remain unchanged. CLI: `tools/evaluate_offline.py`, `tools/verify_review.py`.

`verify: uv run pytest -q -m "not live"` — **313 passed, 3 live tests deselected**. Ruff and package build pass. Focused review/evaluation tests: 23 passed; mutations reject wrong/missing/duplicate/extra findings and stale versions, while CLI refusal replaces old green artifacts. Baseline: 26 pass, 5 needs_review, 4 fail, 13 not_evaluable, rulebook `d0550ba69224`. This is fixture agreement, not vision accuracy or comprehensive regulatory coverage.

Workbench's combined suite: **54 browser tests passed** across Chromium/Firefox/WebKit. New review scenarios cover invalid uploads, explicit pending/complete records, unchanged automated failures, download validation, no-JavaScript operation, keyboard, 320/640/1280px and WCAG scans. The downloaded browser record passes the real verifier CLI. All 11 combined PDFs (17 pages) pass text/margins and visual inspection. Evidence: `artifacts/evaluation/`, Workbench `artifacts/browser/`, and `C:/Users/abish/Documents/Codex/2026-11-03/give/outputs/fde-integration/`.

No subagents, remote CI run, pushes or deployments. New features remain local; hosted demo unchanged. Reviewer identity/authentication, representative real-artwork evaluation, physical-device and assistive-speech checks remain explicit limitations.

### 2026-11-04 — Local accessibility and report checks (gpt-6-astra)

Goal: complete the accepted compatibility, regression and accessibility follow-up. Added keyboard skip links (including WebKit's explicit tab stop), announced review-filter counts, persistent per-file comparison errors, focused validation messages, reduced-motion progress and compact printed footers/batch introductions. Existing visual style and rulebook are preserved. `tests/browser_server.py` provides real templates/routes with synthetic completed reports and no AI calls; the browser suite lives in Civic Workbench.

Verification: `uv run ruff check .` passed; `uv run pytest -q` **293 passed**, including the three live reader tests. After final CSS changes, focused web/comparison/contrast tests **45 passed**; `uv build` succeeded. Workbench's all-tools suite **42 passed** across Chromium/Firefox/WebKit; this includes six LabelAssay page layouts, keyboard/filter/upload-error behavior and single/batch/comparison exports. PDF text/margin and visual checks passed. Full evidence: `C:/Users/abish/Documents/Codex/2026-11-03/give/outputs/all-next/NEXT-PASS.md`.

Remaining: physical iPhone/touch, actual screen-reader speech and native print dialogs are not certified. No subagents, push or deployment. Local existing preview remains at port 8790; the isolated test fixture uses 18790.
