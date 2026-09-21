# LabelAssay

Checks alcohol beverage labels against TTB labeling requirements (27 CFR parts 5 and 16; the wine and malt equivalents in parts 4 and 7 are a documented gap). Upload a label image and the details filed on the application; get **compliant / needs review / needs correction**, with the specific rule and CFR citation behind each finding.

**Live:** https://haksanlulz-label-assay.hf.space

## Why It Works This Way

AI does the reading. Plain Python does the deciding.

A vision model transcribes what is on the label. Compliance verdicts are then computed in Python against a rulebook of TTB rules held as data. Two reasons for that split:

- **A model will confidently pass a non-compliant label.** Vision models reproduce the Government Warning from memory instead of reading it. They score near-perfectly on canonical images and badly on altered ones, and a label whose warning is subtly wrong *is* an altered image. So the statutory text is compared deterministically in code, word for word against the model's transcription, with the mandated heading capitals checked exactly. A pass also requires the independent OCR read to contain the statute itself, so a model reciting the paragraph over an altered label is held for review instead of passed.
- **Speed.** A review tool that takes 30 to 40 seconds per label doesn't get used; reviewers fall back to checking by eye. The pipeline is one terse model call and one local OCR pass run concurrently, plus microseconds of deterministic checking, and the result page prints the measured time of every check. Whether the deployed instance clears the 5-second target depends on its CPU budget: see Limitations.

An independent OCR pass reads the same image. Where the two readings disagree, the finding is held for a human instead of passed or failed. The two channels fail in different ways, so their agreement is evidence; a model's self-reported confidence isn't.

## What It Checks

| Check | Citation |
|---|---|
| Health warning statement, verbatim | 27 CFR 16.21 |
| Warning heading in bold, remainder not | 27 CFR 16.22(a)(2) |
| Brand name matches the application | 27 CFR 5.64 |
| Alcohol content internally consistent (proof = 2 × ABV) | 27 CFR 5.65, 5.1 |

An application may also file an optional fanciful name alongside the brand name (both are fields on the COLA form). When one was filed, the brand check accepts the label displaying either filed name: on the single-label form it's the optional "Fanciful name (if filed)" field, in a batch CSV the optional `fanciful_name` column.

Two further fields are read but not checked. Net contents is extracted and available at the extractor port, but no verification rule shipped for it (a standards-of-fill check needs the authorized-container data in 27 CFR part 5 subpart E). Class/type selects which rules apply to a label but is never compared against the application. The result page echoes what was read from the label, and notes that fields outside the checks above are shown for reference only. Bottler name and address, and country of origin, are neither read nor checked.

Every finding carries its citation. Verdicts are **advisory**; a compliance specialist makes the decision.

## Verdict Model

- **Compliant**: every automated check passed.
- **Needs review**: something couldn't be verified automatically. Text that couldn't be read, the two readings disagreeing, or a rule that isn't checkable from an image. Never a silent pass or fail.
- **Needs correction**: a check failed on evidence that was read.

## Data Flow and Privacy

The label image leaves the box. Each check re-encodes the uploaded image to a bounded copy (pixels only, so EXIF metadata such as GPS position is stripped), then base64-encodes that copy and sends it to Anthropic's Claude API (the hosted vision model in `extract/haiku.py`) to transcribe the printed text. The vision adapter is blind by construction: the application data and the OCR read are never sent, and no other field, filename, or file is transmitted. The second read (local OCR) runs offline and sends nothing.

Images are never retained. Single-label checks are processed in memory, and the result page returns the image to the browser as a downscaled `data:` URI instead of a stored copy. Batch uploads are spooled to temp files only for the life of the job and deleted as each label is processed. Batch result rows (filenames, verdicts, and finding details, never images) are held in process memory for the 50 most recent finished jobs so a results page can be revisited, evicted oldest-first and cleared by a restart. No image, extraction, or verdict is written to a database, and server logs record failures with field locations and error types but not the transcribed label text.

The label artwork in scope is public COLA-registry material with no personal data, so the disclosure risk is low. Anthropic's handling of API inputs is governed by its own commercial terms, not by this project; review Anthropic's commercial and data-usage terms (anthropic.com/legal) before sending any non-public content. The outbound dependency is swappable: the extractor port ([docs/adr/0004-swappable-extractor.md](docs/adr/0004-swappable-extractor.md)) exists so a local or in-tenant vision model can replace the hosted API by writing one adapter and changing the one-line selection in `web/service.py`. The compliance engine is untouched.

## Run It

```
uv sync
cp .env.example .env          # then add an ANTHROPIC_API_KEY
uv run uvicorn label_assay.web.app:app --reload
```

Open <http://127.0.0.1:8000>. Health at `/health`, batch at `/batch`. Batch results export as a CSV that carries, after the per-label summary columns, one verdict column per rulebook rule (headers are the rule ids), with an empty cell where a rule produced no finding for that label. The single-label form has a "Label image is rotated" select for sideways or upside-down scans: the image is straightened once before it's read, and the result page echoes the straightened image that was judged. The batch form has a "Retry sideways reads" checkbox, on by default: a label whose government warning isn't found upright is re-read rotated, up to three extra read passes for that label, and unchecking it trades that recovery for maximum throughput.

With Docker:

```
docker compose up             # http://localhost:8080
```

## Testing

Offline suite (measured 2026-09-11, Windows, Python 3.14.4):

```
uv run python -m pytest                              # 267 passed, 3 skipped, about 35 to 40 s
uv run python -m pytest -m "not slow and not live"   # fast tier: 265 tests
uv run python -m pytest -m live                      # 3 tests that call the Anthropic API; skip without ANTHROPIC_API_KEY
uv run python -m pytest -m slow                      # 2 tests over 5 s: real OCR over the committed corpus
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

**A batch upload is capped at 1.6 GB total and streams to disk.** Each file is spooled to a named temp file as it uploads, with the 5 MB per-file cap and the image magic-byte check enforced during the copy; a worker reads one file's bytes back only while checking that label and deletes the temp file afterward. Peak memory is therefore the worker concurrency times one file, and a full 300-application drop at the per-file cap (~1.5 GB) fits in one upload. The 1,000-file sanity ceiling is unchanged; an oversized upload is asked to split, and the upload page states the limits. The 1.6 GB figure is a tunable disk guard sized for this single free-tier instance. Because memory scales with worker concurrency and not batch size, a larger host raises the cap by changing one constant, and scaling past a single machine is a matter of swapping the in-memory job store for a shared one (see DESIGN's trade-offs).

**Single-label latency is bound by the slower of the two reads.** The OCR pass and the vision-model call run concurrently, so a check costs the slower one. The result page prints the measured time for every check. Typical deployed single-label checks measured 3.4 to 4.6 seconds, inside the 5-second target, while large multi-panel real composites have measured ~5 to 11 seconds for the same label across runs on the shared free host (the tail is host contention and provider latency; see DESIGN). During a running batch, single-label checks take priority at the serialized OCR stage: a batch worker parks while an interactive check is pending, and a worker that was already queued inside the engine lock hands the lock back unused when it acquires it, so a mid-batch check waits behind at most the one OCR inference already running, and never behind the workers a saturated batch keeps queued at the lock. The mechanism and its trade (sustained interactive traffic pauses batch progress) are documented at the gate in `src/label_assay/extract/ocr.py`, and a test pins the schedule at the deployed worker count. Measured during a running 300-label batch on the deployed host, a mid-batch interactive check took 5.0 seconds, which is at the "about 5 seconds" bar under full CPU saturation and not comfortably under it. Idle checks measure 3.4 to 4.6 seconds, and the first request after a deploy measured 3.75 seconds thanks to the startup warm. The app fires one budget-accounted warm extraction at startup when a key is configured, so the first check after a restart doesn't pay the provider's connection and model cold start.

**Hosting sleeps when idle.** The deployed instance runs on a free-tier Space that suspends after long inactivity. A scheduled health check (.github/workflows/uptime.yml) probes it every six hours, which keeps it warm and emails on any degraded subsystem. If it ever lapses, the first request pays a cold start of about a minute.


Full list, with reasoning: [docs/DESIGN.md](docs/DESIGN.md).

## Approach, Tools, and Assumptions

See [docs/DESIGN.md](docs/DESIGN.md) and the decision records in [docs/adr/](docs/adr/).

This project was built with AI assistance (Claude). Every regulatory value was verified against the primary source (eCFR and the U.S. Code) and none was taken from the model. Tests were written alongside the code they cover, and the author is accountable for every line. All but three commits carry an `Assisted-by:` trailer. Details in DESIGN.
