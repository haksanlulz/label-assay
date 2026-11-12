### 2026-11-12 — Audit with refuters, and the fixes it led to (Fable 5.2)

Branch `audit-2026-11`, five commits on top of `5098adc`. Not merged, not pushed, not deployed.

Start: a TTB-approved filing whose heading is printed `GOVERMENT WARNING` (`tests/fixtures/cola/cola_24100001000120.png`) came back "needs review" with no pointer. The reader had written the correct text; the scan had read the misprint at 0.98 confidence and that was thrown away. A held warning now names the words the scan read differently. The reader was not fixed: it corrects the misprint 3 of 3 times on the shipped prompt, with a letter-by-letter instruction, and on `claude-haiku-5-5`. The prompt is unchanged; the live test recording this is marked xfail.

Then an audit: five read-only lanes (readers, deciding layer, server, pages, tooling), each finding checked by two refuters. 122 findings; 90 confirmed by both, 2 refuted. Fixed here, each reproduced as a failing test first:

- Deciding layer: a reader slip no longer fails a label the scan read correctly; a recited warning no longer passes over words added on the statement's own line; decimal commas parse (`11,5%` was read as 5%, `40,0% ... 80 PROOF` failed); the heading locator no longer raises; a demoted finding keeps its differences.
- Readers: 16-bit grayscale scans were blank to both readers; transparent artwork was black to the scan; `DAILY_BUDGET_USD=nan` switched the spend bound off.
- Checks that could not fail: live tests skipped on a rejected key; `verify_deploy.py` accepted a page missing rules and never compared the time to the 5-second target; `eval_cola.py` always exited 0; the batch cancel test never cancelled anything (now fails with the cleanup removed). New import scan for the deciding layer, with a planted-fault test.
- Server: the CSV export served unfinished batches; a CSV headed "Brand Name" silently skipped every brand check; `/compare` printed the interpreter's stack message.
- Pages: the batch table was rebuilt every 1.5 s, dropping keyboard focus and re-announcing three live regions; polling had no back-off; script-only buttons showed without scripting. `tools/browser_check.cjs` drives these in Chromium, Firefox and WebKit; the old script fails it in all three.

Validation: `pytest -m "not live"` 448 passed; ruff clean; offline gate 14 cases / 56 decisions unchanged; browser check passes in three engines. The new added-words hold fires on 0 of 20 committed labels whose statement scans exactly. Nine paid reader calls during the reader experiments.

Not done:
- The 2026-11-10 scored run describes `c515dc3`. The verdict logic has changed since (two new holds, one fail turned into a hold); the 100 labels have not been re-run on it.
- The live tests were not run after the change that makes a rejected key fail them.
- CI files, unprovable locally: `hf-deploy.yml` goes green without waiting for the Space or running `verify_deploy.py`; `uptime.yml` accepts any verdict and only warns on speed; the deploy gate's `uv sync` is not `--locked`.
- About 85 confirmed findings are untouched, most low severity: beverage class inferred from substrings, the rulebook loader ignoring unknown keys, `/health` reporting ok with the budget spent, no rate limit on the paid route, container hardening, page semantics. The full list with both refuters' notes is outside this repo.
- README line on code size is still the older dated figure.

### 2026-11-10 — A 100-label scored run with a kept ledger (Fable 5.2)

DESIGN cited a 2026-07-17 100-label run as 49 defective and 51 compliant with "19 passed, 31 abstained". Those two numbers cover 50 of the 51, and no per-label results from that run exist in the repo or its history. Operator ruling: re-run and keep the ledger. A fresh corpus (`make_test_labels.py --seed 20261110 --count 100`) went through `/batch` on a local instance at `c515dc3` with the live reader: 0 false passes across 50 defective labels (36 failed, 14 held), 0 false fails across 50 compliant ones (26 passed, 24 held), no processing errors. `docs/evaluation/synthetic-100-2026-11-10/` holds the ledger, manifest and applications; `tools/score_batch.py` is the scorer; DESIGN cites this run and says what the earlier one lacked.

Not changed: the README's 300-label paragraph. That run's per-label results were not kept either; it has no arithmetic gap, but it is equally unreproducible from the record. The 24 holds on compliant labels are mostly warning corroboration and borderline heading weight (see the ledger's `detail` column).

Validation: `uv run pytest -q` 379 passed, 3 skipped; ruff check and format clean on the new tool. One paid step: 100 reader calls plus the warm-up. No push or deployment.

### 2026-11-06 — Alcohol-content review details say why (Fable 5.1)

Audit findings LA-2 and LA-5 (`F:/claude/.coord/reviews/fable-audit-codex-2026-11-06.md`). `parse_alcohol_content` returned None for absent, conflicting, malformed and out-of-range claims alike, so the engine reported the 11-05 headline catch ("80 PROOF" beside "100 PROOF") as "Could not read the alcohol content to check it." `read_alcohol_content` now returns an `AlcoholReading` with the content or the reason, and the finding reads "Could not check the alcohol content: the label states more than one proof (80 proof and 100 proof)."; the absent case keeps its wording, so the evaluation baseline is unchanged. The review note on findings held because OCR read nothing no longer claims "a second reading of the label did not corroborate this"; it says the OCR scan could not read the label. Verdicts unchanged; details only.

Validation: `uv run pytest -q -m "not live"` 379 passed, 3 deselected; ruff clean; the two new engine tests fail with the old wording restored. No paid APIs, push or deployment.

### 2026-11-05 — Ambiguous alcohol-field claims (gpt-6.1-sol)

Reproduced the remaining parser concern through the actual service and real OCR: a generated otherwise-compliant label printed `40% ALC/VOL (80 PROOF) (100 PROOF)`, both readers supplied both proof claims, and all four findings passed because the parser used only the first proof. Parsing now requests review for conflicting readable ABV/proof values, unreadable explicit alcohol-unit/proof markers, signed negative ABV and proof values outside 0–200. Equivalent repetitions and existing `100% DE AGAVE` inputs remain checkable. Peer challenges also cover explicit `100%`/`999%` alcohol claims and OCR-letter substitutions such as `4O% ALC/VOL`. These are bounded patterns rather than an exhaustive OCR grammar. The change covers claims included in the extracted alcohol field; it does not detect statements omitted by both readers or establish exhaustive transcription of the artwork. No paid APIs, push or deployment.

Validation: focused numbers/service/engine suite 72 passed, 2 live tests deselected; full offline suite 369 passed, 3 live tests deselected; offline evaluation 14 scenarios / 56 decisions passed; Ruff and whitespace checks passed. Independent peer challenge (gpt-6.1-terra) agreed after checking malformed explicit claims, duplicate-value positives and agave compatibility.

### 2026-11-05 — Independent audit and safety fixes (gpt-6.1-terra, gpt-6.1-sol)

An independent paired audit found that model `value` and quoted text could disagree while the brand finding passed, fuzzy OCR could overlook changed alcohol numbers, empty OCR could leave a decisive finding, and a recited uppercase warning could pass over a title-case heading. The gate now corroborates both model fields, checks alcohol numbers and units in order, holds assertions for review when supplied OCR is unreadable, and independently checks the heading capitals on the corroborated statement. The intentionally OCR-free engine mode remains available. A match only to a filed fanciful name now requests review of the required brand.

The HTTP upload ceiling now counts streamed bytes even without a valid declared length; oversized or disconnected multipart uploads close partial parser files. One batch per process can upload or run at a time, with its slot retained through cleanup; four other POSTs remain available under smaller body caps. Cancelled batch jobs wait for running worker threads before final cleanup. The batch parser accepts 1,000 images plus the optional CSV. Deeply nested compare JSON is rejected cleanly. Health remains HTTP 200 for liveness but reports degraded when the reader is unconfigured. Limits are per process and assume the documented single-instance deployment. Direct cancellation while Starlette is awaiting its multipart file write remains dependent on upstream cleanup behavior.

The offline gate now includes independent heading-case and numeric-conflict cases: 14 scenarios / 56 decisions. Real OCR regression proof uses the title-case `label_008.png` with a fake reciting reader. Final local suite: 345 passed, 3 skipped; peer-focused suite: 201 passed, 2 live deselected; Ruff, offline gate and package build passed. No paid model calls or deployment. The gate remains fixture agreement, not a production accuracy or legal approval measure.

### 2026-11-05 — Portable review byte limits (gpt-6.1-sol)

Fixed a boundary defect for large imported reports: the web form could download a valid review larger than the verifier's former 8 MiB ceiling. Current four-rule engine reports do not normally approach this boundary. Review serialization and loading now share a 32 MiB encoded-record limit, sufficient for the accepted 5 MiB source plus all permitted notes with UTF-8 or ordinary ASCII JSON escaping. The CLI reads at most that limit plus one byte; export checks its final encoded bytes. Decision text is preserved. URL-encoded form parsing now allows percent-escape overhead while retaining the decoded 5 MiB source limit.

Regression proof imports a near-5-MiB Unicode source with 500 findings, exports maximum-length per-finding and overall notes through HTTP, checks every original finding/note, and runs the real verifier CLI against both UTF-8 and ASCII-escaped records. Oversized CLI input remains rejected. Focused review/evaluation tests: 35 passed; adjacent web/comparison tests: 41 passed; Ruff passed. Existing schema versions, report digests, reviewer identity limitations and stateless operation are unchanged. No push or deployment.

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
