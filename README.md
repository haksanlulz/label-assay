# LabelAssay

Checks alcohol beverage labels against TTB labeling requirements (27 CFR parts 5 and 16; the wine and malt equivalents in parts 4 and 7 are a documented gap). Upload a label image and the details filed on the application; get **compliant / needs review / needs correction**, with the specific rule and CFR citation behind each finding.

**Live:** https://haksanlulz-label-assay.hf.space

**Added in 0.2.0:** keyboard skip links, announced batch-filter counts and per-file comparison errors accompany the review queue, saved revision comparisons and printable reports. They are part of the deployed build as of 2026-11-12. The Civic Workbench `start-local.ps1` launcher can start both apps; point `LABEL_ASSAY_ROOT` at this checkout if it is not under `~/code/label-assay`.

For repeatable UI checks, `uv run python tests/browser_server.py` serves the real app with three synthetic completed reports at `http://127.0.0.1:18790/batch/browser-fixture`. It disables reader warm-up and makes no AI calls. Civic Workbench's `npm run test:browser:all` owns the Chromium/Firefox/WebKit suite and automatically starts/stops this fixture. Its `npm run test:pdf` validates the resulting single-label, batch and revision PDFs alongside the civic reports. Physical devices, actual screen-reader speech and native print dialogs remain separate checks.

## Why It Works This Way

AI does the reading. Plain Python does the deciding.

A vision model transcribes what is on the label. Compliance verdicts are then computed in Python against a rulebook of TTB rules held as data. Two reasons for that split:

- **A model will confidently pass a non-compliant label.** Vision models reproduce the Government Warning from memory instead of reading it. They score near-perfectly on canonical images and badly on altered ones, and a label whose warning is subtly wrong *is* an altered image. So the statutory text is compared deterministically in code, word for word against the model's transcription, with the mandated heading capitals checked exactly. A pass also requires the independent OCR read to contain the statute itself, so a model reciting the paragraph over an altered label is held for review instead of passed.
- **Speed.** A review tool that takes 30 to 40 seconds per label doesn't get used; reviewers fall back to checking by eye. The pipeline is one terse model call and one local OCR pass run concurrently, plus microseconds of deterministic checking, and the result page prints the measured time of every check. Whether the deployed instance clears the 5-second target depends on its CPU budget: see Limitations.

An independent OCR pass reads the same image. Where the two readings disagree, the finding is held for a human instead of passed or failed. Numeric alcohol evidence must agree in value and stated units; the OCR must also confirm the warning heading's capital letters. An unreadable OCR pass leaves asserted fields for review. The two channels fail in different ways, so their agreement is evidence; a model's self-reported confidence isn't.

## What It Checks

| Check | Citation |
|---|---|
| Health warning statement, verbatim | 27 CFR 16.21 |
| Warning heading in bold, remainder not | 27 CFR 16.22(a)(2) |
| Brand name matches the application | 27 CFR 5.64 |
| Alcohol content internally consistent (proof = 2 × ABV) | 27 CFR 5.65, 5.1 |

The alcohol check requests review when the extracted field contains conflicting readable ABV or proof values, an unreadable explicit alcohol-unit or proof claim, or a proof outside 0–200. Equivalent repeated values remain checkable, and `100% DE AGAVE` is not treated as ABV. These bounded patterns cover the supplied alcohol field; they are not an exhaustive OCR grammar and cannot establish that the readers included every statement printed elsewhere on the artwork.

An application may also file an optional fanciful name alongside the brand name (both are fields on the COLA form). If the reader finds only the fanciful name, the brand check asks a person to confirm the required brand rather than treating the fanciful name as its substitute. Enter it in the optional single-label field or the batch CSV's `fanciful_name` column. [TTB's brand-name guidance](https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/ds-labeling-home/ds-brand-name) describes the distinct requirements for distilled spirits.

Two further fields are read but not checked. Net contents is extracted and available at the extractor port, but no verification rule shipped for it (a standards-of-fill check needs the authorized-container data in 27 CFR part 5 subpart E). Class/type selects which rules apply to a label but is never compared against the application. The result page echoes what was read from the label, and notes that fields outside the checks above are shown for reference only. Bottler name and address, and country of origin, are neither read nor checked.

Every finding carries its citation. Verdicts are **advisory**; a compliance specialist makes the decision.

## Verdict Model

- **Compliant**: every automated check passed.
- **Checks incomplete** (single-label and batch detail pages): the checks that ran passed, but one or more findings are **Not checked**. The page uses the review color and names the number of unavailable checks. Batch rows explain skipped checks in their detail text; the underlying report verdicts and compliance rules are unchanged.
- **Needs review**: something couldn't be verified automatically. Text that couldn't be read, the two readings disagreeing, or a rule that isn't checkable from an image. Never a silent pass or fail.
- **Needs correction**: a check failed on evidence that was read.

## Data Flow and Privacy

Batch results now offer **View findings** for every checked label, with the same citations, explanations, and word differences as a single-label result. Each detail page identifies the uploaded filename and the rulebook version used for that check. **Full report (JSON)** downloads a completed batch's findings, rulebook versions, and error rows; **Export CSV** remains the compact summary and per-rule verdict grid. The JSON export has `schema_version: 1` and preserves errors as rows with `report: null`. An unfinished batch returns HTTP 409 instead of presenting a partial export as finished. Download a report to keep it after the in-memory batch expires.

The label image leaves the box. Each check re-encodes the uploaded image to a bounded copy (pixels only, so EXIF metadata such as GPS position is stripped), then base64-encodes that copy and sends it to Anthropic's Claude API (the hosted vision model in `extract/haiku.py`) to transcribe the printed text. The vision adapter is blind by construction: the application data and the OCR read are never sent, and no other field, filename, or file is transmitted. The second read (local OCR) runs offline and sends nothing.

Images are never retained. Single-label checks are processed in memory, and the result page returns the image to the browser as a downscaled `data:` URI instead of a stored copy. Batch uploads are spooled to temp files only for the life of the job and deleted as each label is processed. Batch result rows and reports (filenames, verdicts, finding details, citations, word differences, and rulebook versions, never images or full extractions) are held in process memory for the 50 most recent finished jobs so results and exports can be revisited, evicted oldest-first and cleared by a restart. Finding details and differences can contain excerpts from the label and filed application. No image, extraction, or verdict is written to a database, and server logs record failures with field locations and error types but not the transcribed label text.

The label artwork in scope is public COLA-registry material with no personal data, so the disclosure risk is low. Anthropic's handling of API inputs is governed by its own commercial terms, not by this project; review Anthropic's commercial and data-usage terms (anthropic.com/legal) before sending any non-public content. The outbound dependency is swappable: the extractor port ([docs/adr/0004-swappable-extractor.md](docs/adr/0004-swappable-extractor.md)) exists so a local or in-tenant vision model can replace the hosted API by writing one adapter and changing the one-line selection in `web/service.py`. The compliance engine is untouched.

## Review and Compare Saved Checks (0.2.0)

The batch **Review queue** includes corrections, human-review findings, unchecked rules and processing errors. Separate filters show **Uncheckable findings** and **Could not be read**. A label whose available checks passed but has an uncheckable finding appears as **Checks incomplete**, rather than under Compliant; underlying rule verdicts remain unchanged.

Single-label reports and `/batch/{id}/print` provide printable evidence with check timestamps, rulebook versions, citations and limitations. Use browser Print to save a PDF. Older reports without timestamps say so explicitly. Finished JSON exports retain each label's `checked_at` alongside its full findings.

Individual result pages also save comparison-ready JSON. Printable HTML downloads bundle their styling and omit scripts and uploaded-image previews, so the findings can be opened and printed in a regular browser when an embedded browser's print preview is unavailable.

At `/compare`, upload two full-report JSON exports (maximum 5 MB each), then select the earlier and corrected label rows. The comparison shows findings that now pass, remaining issues, new/removed checks, and uncheckable findings. Changed or unknown rulebook identifiers and differing or unknown declared app versions are flagged. A missing rulebook field makes an import invalid; a blank identifier is unknown. A matching version label does not prove that the software, OCR or model behaved identically, and a changed finding does not prove the artwork caused it. Confirm both reports describe the same product and intended application details. Comparison does not call a model, recheck artwork, authenticate imported reports, or establish regulatory approval. Uploaded reports are compared in memory and not retained.

This candidate is local only and has not been deployed to the hosted demo.

### Record a human review

At `/review`, open a completed row from a saved full-report JSON file (up to 5 MB). Read the original findings, then record a reviewer reference, per-finding decisions and supporting notes. Decisions start **Pending review**; other choices are **Agree with finding**, **Request correction** and **Escalate to a specialist**. The form works without JavaScript for the first label row.

Version 2 review downloads embed the normalized automated report, declared app version (or null when absent), and a SHA-256 digest covering both, along with the self-entered reviewer reference, UTC export time, notes and decisions. The verifier also accepts version 1 records using their original digest; those records did not preserve app version. Pending findings make `complete` false. A complete review is not regulatory approval, and a human decision never replaces an automated verdict. Reports and reviewer identities are not authenticated; the digest checks report consistency, not authorship or deliberate tampering. No model is called and the app keeps no review history. Retain the downloaded file.

Validate a downloaded record with `uv run python tools/verify_review.py path/to/label-assay-review.json`. Missing/duplicate decisions, unknown choices, inconsistent completion and a mismatched report digest return a nonzero exit. Canonical ownership: `domain/review.py`; stateless HTTP forms: `web/review.py`.

Review downloads and the verifier share a 32 MiB encoded-record limit. This accommodates a 5 MiB source plus all permitted notes, including Unicode and JSON escape expansion, without truncation. The verifier reads at most the limit plus one byte; formatting or padding that takes an external file beyond the limit is rejected.

### Reproducible offline evaluation

`uv run python tools/evaluate_offline.py` checks the real rule engine against `tests/fixtures/evaluation/cases.json`: **12 synthetic text/OCR-evidence scenarios, 48 rule decisions**, pinned to a reviewed rulebook version. Each case specifies every expected per-rule and overall verdict. The gate rejects missing, duplicate or additional findings, changed verdicts and stale/incomplete baselines. Tests inject those faults and prove the gate fails.

Results go to ignored `artifacts/evaluation/results.json` and `summary.md`, with a baseline digest, full findings and per-rule expected/actual matrices. The November 4 baseline gives 26 passes, 5 needs-review findings, 4 failures and 13 not-evaluable findings. Abstentions remain in the accounting. This measures rule-engine agreement on supplied evidence; it does **not** measure vision accuracy, image boldness, comprehensive regulatory coverage or production approval. The existing image/OCR and real COLA corpus tests cover separate concerns.

`.github/workflows/offline-evaluation.yml` runs the focused gate on pull requests or manual dispatch and has no deployment job. It is a local workflow definition until published; no remote run is claimed. Review intended behavior before changing the manifest's expectations or rulebook version. A failed evaluation replaces any previous green output with failure evidence when the output directory is writable. Evidence expectations cover selected findings, including one boldness abstention, rather than every finding in every case.

## Run It

```
uv sync
cp .env.example .env          # then add an ANTHROPIC_API_KEY
uv run uvicorn label_assay.web.app:app --reload --port 8001
```

Open <http://127.0.0.1:8001>. Health at `/health`, batch at `/batch`. Batch results export as a CSV that carries, after the per-label summary columns, one verdict column per rulebook rule (headers are the rule ids), with an empty cell where a rule produced no finding for that label. The single-label form has a "Label image is rotated" select for sideways or upside-down scans: the image is straightened once before it's read, and the result page echoes the straightened image that was judged. The batch form has a "Retry sideways reads" checkbox, on by default: a label whose government warning isn't found upright is re-read rotated, up to three extra read passes for that label, and unchecking it trades that recovery for maximum throughput.

Application CSVs must be UTF-8, with one row per image and unique column names. Filename pairing ignores folders and capitalization, so `labels/A.PNG` and `a.png` collide; duplicate CSV rows or uploaded filenames are rejected before a job starts. Duplicate columns, invalid UTF-8, and extra unquoted cells also produce an actionable error instead of silently changing the application data. Fields containing commas must be quoted.

With Docker:

```
docker compose up             # http://localhost:8080
```

## Testing

Offline suite (measured 2026-09-11, Windows, Python 3.14.4):

```
uv run python -m pytest -m "not live"                # 448 tests on 2026-11-12, about 45 s
uv run python -m pytest -m "not slow and not live"   # fast tier: 444 tests
uv run python -m pytest -m live                      # 4 tests that call the Anthropic API; skip without ANTHROPIC_API_KEY, fail on a key the API rejects
uv run python -m pytest -m slow                      # 4 tests over 5 s: real OCR over the committed corpus
```

Markers are registered in `pyproject.toml` under `--strict-markers`. The 3 `live` tests are the only ones that reach the network; everything else runs against the committed fixtures and stubbed extractors.

Size on the same date: app 2,924 lines (`find src -name '*.py' -not -path '*/__pycache__/*' | xargs wc -l`) plus 1,128 in `tools/`; tests 4,642 lines (`find tests -maxdepth 1 -name '*.py' | xargs wc -l`); 270 tests collected.

By layer: text and number parsing (`test_normalize`, `test_numbers`); warning and brand matching and the bold check on rendered pixels (`test_warning`, `test_brand`, `test_bold`); the verdict engine, rulebook data, and OCR scheduling (`test_engine`, `test_ssot`, `test_priority`); the FastAPI routes, batch jobs, spend guard, upload guards, and stylesheet contrast through `TestClient` (`test_web`, `test_batch`, `test_service`, `test_budget`, `test_contrast`). The synthetic corpus generator is checked against the engine itself (`test_make_labels`) and the 11 real COLA labels pin the no-false-positive direction (`test_cola_corpus`, `test_bold`).

Mutation probe, 2026-09-11: flipping `AlcoholContent.proof_matches_abv` in `src/label_assay/text/numbers.py` from `==` to `!=` failed `tests/test_numbers.py::test_parses_abv_and_proof_from_sample_label`, `::test_inconsistent_proof_is_flagged`, and `::test_marketing_percent_is_not_truncated_into_an_abv`, and broke collection of 7 modules (`test_batch`, `test_bold`, `test_engine`, `test_extraction`, `test_rotation`, `test_service`, `test_web`) because the corpus builder they import asserts proof consistency at build time. The file was restored and is byte-identical to HEAD.

Wiring audit, same date: no `assert_called` or `assert_awaited` anywhere; 4 tests record calls on hand-rolled stubs (7 assertions against the recorders), 0 pruned. Each guards a contract (nothing is spent on a rejected upload or a decompression bomb; the startup warm fires exactly once against the real budget at background priority). Policy: assert behavior and payloads, never that a function was called.

## Deploying

Pushes to `main` deploy automatically. `.github/workflows/hf-deploy.yml` runs `uv run pytest` and `uv run ruff check .` in a `test` job, and only when both pass does the `deploy` job push a one-commit build snapshot of the tree to the Hugging Face Space, which builds and runs the same Docker image as a single container (the in-process batch job store requires exactly one instance). A second workflow, `.github/workflows/uptime.yml`, probes the deployed instance on a six-hour schedule and exercises one end-to-end check against a committed fixture, so a degraded subsystem emails and the free-tier Space never idles into sleep. A push is the only thing that moves the deployment. Local commits that aren't pushed, or a green suite on uncommitted work, leave the public URL serving older code with no local signal. After every push:

```
uv run python tools/verify_deploy.py                 # against the deployed Space URL
uv run python tools/verify_deploy.py --health-only   # same, without spending a model call
```

It fails unless `/health` answers with every subsystem ready and this tree's exact rulebook hash, removed routes 404, and one timed check of a known-compliant fixture comes back compliant through the deployed instance. The Live link above is only claimed after this passes. A dropped connection means the Space is stopped or still building; the deploy workflow can be re-run by hand from the Actions tab.

## Test Labels

`tools/make_test_labels.py` generates the corpus in `tests/fixtures/labels/`: 24 synthetic labels spanning distilled spirits, wine, and malt classes, four layouts, six palettes, several font families, and four canvas sizes, with invented brands in varied casings. About half are compliant. Two of those render the entire warning statement in capitals, which is legal because 27 CFR 16.22(a)(2) fixes only the heading's case. The rest each carry one specific defect mapped to a check the engine performs: warning heading in title case, heading not bold, altered statutory text, missing warning, proof ≠ 2 × ABV, and a label brand differing from the filed brand (both a typo-level variant and a different brand).

Two CSVs ride along:

- `applications.csv` (`filename,brand_name,class_type`): the data *filed* for each label. On the brand-mismatch labels the filed brand deliberately differs from what is painted.
- `manifest.csv` (`filename,defect,expected_verdict,notes`): ground truth for tests and evaluation. The app never reads it.

Regenerate with `uv run python tools/make_test_labels.py`. Output is deterministic for a given `--seed` on one machine; fonts differ across machines, so committed bytes are machine-specific.

To try the app with them: upload any fixture PNG on the single-label page together with its `brand_name` and `class_type` row from `applications.csv`, or upload several PNGs plus `applications.csv` itself on `/batch`. `manifest.csv` says what each label should produce on a faithful read; through the live app, the OCR corroboration can also hold a finding for review when the second read of a dark or decorative label is degraded.

A second, real corpus lives in `tests/fixtures/cola/`: 11 label filings from TTB's public COLA Registry, one composite PNG per application, with the brand, fanciful name, and class/type as filed. All eleven were approved by TTB, which makes them ground truth in one direction. `uv run python tools/eval_cola.py` uploads them through a running instance's `/batch` endpoint and reports per-label outcomes, where a content-rule **fail** is a candidate false positive, **needs review** is legitimate abstention (rotated and low-contrast warnings are in the set on purpose), and bold-check findings are soft ground truth because the registry disclaims the rendered typography. One documented exception: `cola_24100001000120` misspells the mandated heading as `GOVERMENT WARNING` on the label itself (approval doesn't guarantee textual perfection), so a wording **fail** on that row is a true positive. Provenance and per-label notes: [tests/fixtures/cola/README.md](tests/fixtures/cola/README.md).

## Layout

- `src/label_assay/domain/`: the domain model. No I/O.
- `src/label_assay/rulebook/`: TTB rules **as data** (`rules/*.yaml`) plus the loader. Each rule carries a required CFR citation; `tests/test_ssot.py` fails if statutory text is hardcoded anywhere in the source.
- `src/label_assay/text/`, `.../match/`: normalization, the warning comparator, brand matching, bold detection. Pure functions.
- `src/label_assay/extract/`: the extractor port and its adapters (vision model, OCR, fixture replay).
- `src/label_assay/verify/`: the compliance engine and the legibility gate.
- `src/label_assay/web/`: FastAPI app, templates, batch runner, spend guard.

## Limitations

Rules regulated in millimetres (type size, characters per inch, contrasting background) are **not** checked. A flat image carries no physical scale, so they're unverifiable from the artifact and are reported as not evaluable instead of guessed.

**Batch throughput is bound by the host CPU.** Every label runs local OCR, a detection and recognition pass, which is sustained CPU work. On the previous host, a burst-credit shared-CPU tier, this measured ~2.3s per label until the credits ran out and 20 to 30s per label after, which is why a 300-label batch couldn't complete there. The current host runs two full vCPUs with no burst mechanic, so the sustained rate is the steady rate. Measured on this host: a 100-label batch completes in 213 seconds through the deployed `/batch`, 2.1s per label sustained, the rate improving as the run warms, zero errors; the 11-label real-registry corpus, whose scans are far larger images, averages ~4.5s per label. The stated peak volume has now been run for real: 300 labels in one 15 MB upload, accepted in half a second, completed in 15.0 minutes with zero processing errors and, scored per label against the corpus manifest, zero false passes and zero false fails.

**A batch upload is capped at 1.6 GB total.** The multipart parser first spools the request; the app then copies accepted images to named temporary files, applying the 5 MB per-file cap and magic-byte check during that copy. The request stream itself is capped at 1.7 billion bytes, including multipart framing, even without a trustworthy Content-Length. At most one batch can be uploading or processing per process; another receives a temporary busy response. This bounds the batch's combined parser and accepted-file spool to about 3.4 GB at peak. Up to four other POST requests can run concurrently, each capped at 32 MiB; interactive checks remain available during a batch. A worker reads one image at a time and deletes it afterward. A full 300-application drop at the per-file cap (~1.5 GB) fits in one upload. The 1,000-file sanity ceiling is unchanged. These bounds are per process, matching the documented single-instance deployment; a multi-process deployment needs shared admission control and job storage.

**Single-label latency is bound by the slower of the two reads.** The OCR pass and the vision-model call run concurrently, so a check costs the slower one. The result page prints the measured time for every check. Typical deployed single-label checks measured 3.4 to 4.6 seconds, inside the 5-second target, while large multi-panel real composites have measured ~5 to 11 seconds for the same label across runs on the shared free host (the tail is host contention and provider latency; see DESIGN). During a running batch, single-label checks take priority at the serialized OCR stage: a batch worker parks while an interactive check is pending, and a worker that was already queued inside the engine lock hands the lock back unused when it acquires it, so a mid-batch check waits behind at most the one OCR inference already running, and never behind the workers a saturated batch keeps queued at the lock. The mechanism and its trade (sustained interactive traffic pauses batch progress) are documented at the gate in `src/label_assay/extract/ocr.py`, and a test pins the schedule at the deployed worker count. Measured during a running 300-label batch on the deployed host, a mid-batch interactive check took 5.0 seconds, which is at the "about 5 seconds" bar under full CPU saturation and not comfortably under it. Idle checks measure 3.4 to 4.6 seconds, and the first request after a deploy measured 3.75 seconds thanks to the startup warm. The app fires one budget-accounted warm extraction at startup when a key is configured, so the first check after a restart doesn't pay the provider's connection and model cold start.

**Hosting sleeps when idle.** The deployed instance runs on a free-tier Space that suspends after long inactivity. A scheduled health check (.github/workflows/uptime.yml) probes it every six hours, which keeps it warm and emails on any degraded subsystem. If it ever lapses, the first request pays a cold start of about a minute.


Full list, with reasoning: [docs/DESIGN.md](docs/DESIGN.md).

## Approach, Tools, and Assumptions

See [docs/DESIGN.md](docs/DESIGN.md) and the decision records in [docs/adr/](docs/adr/).

This project was built with AI assistance (Claude). Every regulatory value was verified against the primary source (eCFR and the U.S. Code) and none was taken from the model. Tests were written alongside the code they cover, and the author is accountable for every line. All but three commits carry an `Assisted-by:` trailer. Details in DESIGN.
