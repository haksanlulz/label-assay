# LabelAssay

Checks alcohol beverage labels against TTB labeling requirements (27 CFR parts 5 and 16; the wine and malt equivalents in parts 4 and 7 are a documented gap). Upload a label image and the details filed on the application; get **compliant / needs review / needs correction**, with the specific rule and CFR citation behind each finding.

**Live:** https://haksanlulz-label-assay.hf.space

## Why it works this way

The reading is done by AI. The deciding is not.

A vision model transcribes what is on the label. Compliance verdicts are then computed in plain Python against a rulebook of TTB rules held as data. Two reasons drove that split:

- **A model will confidently pass a non-compliant label.** Vision models reproduce the Government Warning from memory instead of reading it — they score near-perfectly on canonical images and badly on altered ones. A label whose warning is subtly wrong *is* an altered image. So the statutory text is compared deterministically in code, word-for-word against the model's transcription, with the mandated heading capitals checked exactly — and a pass additionally requires the independent OCR read to contain the statute itself, so a model reciting the paragraph over an altered label is held for review, not passed.
- **Speed.** The prior vendor took 30–40 seconds per label, so reviewers went back to checking by eye. The pipeline is one terse model call and one local OCR pass run concurrently, plus microseconds of deterministic checking, and the result page prints the measured time of every check. Whether the deployed instance clears the 5-second target depends on its CPU budget: see Limitations.

An independent OCR pass reads the same image. Where the two readings disagree, the finding is held for a human rather than passed or failed — the two channels fail in different ways, so their agreement is real evidence in a way a model's self-reported confidence is not.

## What it checks

| Check | Citation |
|---|---|
| Health warning statement, verbatim | 27 CFR 16.21 |
| Warning heading in bold, remainder not | 27 CFR 16.22(a)(2) |
| Brand name matches the application | 27 CFR 5.64 |
| Alcohol content internally consistent (proof = 2 × ABV) | 27 CFR 5.65, 5.1 |

An application may also file an optional fanciful name alongside the brand name (both are fields on the COLA form), and when one was filed the brand check accepts the label displaying either filed name — on the single-label form it is the optional "Fanciful name (if filed)" field, in a batch CSV the optional `fanciful_name` column.

Every finding carries its citation. Verdicts are **advisory** — a compliance specialist makes the decision.

## Verdict model

- **Compliant** — every automated check passed.
- **Needs review** — something could not be verified automatically: text that could not be read, the two readings disagreeing, or a rule that is not checkable from an image. Never a silent pass or fail.
- **Needs correction** — a check positively failed on evidence that was actually read.

## Run it

```
uv sync
cp .env.example .env          # then add an ANTHROPIC_API_KEY
uv run uvicorn label_assay.web.app:app --reload
```

Open <http://127.0.0.1:8000>. Health at `/health`, batch at `/batch`.

With Docker:

```
docker compose up             # http://localhost:8080
```

Tests:

```
uv run pytest                 # live-API tests skip cleanly without a key
```

## Deploying

Pushes to `main` deploy automatically (`.github/workflows/fly-deploy.yml`), pinned to a single instance because batch job state lives in the process. The workflow runs `uv run pytest` and `uv run ruff check .` first and only deploys when both pass. A push is the only thing that moves the deployment, so anything short of one — local commits not pushed, a green suite on uncommitted work — leaves the public URL serving older code with no local signal. After every push:

```
uv run python tools/verify_deploy.py                 # base URL comes from fly.toml
uv run python tools/verify_deploy.py --health-only   # same, without spending a model call
```

It fails unless `/health` answers with every subsystem ready and this tree's exact rulebook hash, removed routes actually 404, and one timed check of a known-compliant fixture comes back compliant through the deployed instance. The Live link above is only claimed after this passes; a dropped connection means the machine is stopped, and the workflow can be re-run by hand from the Actions tab.

## Test labels

`tools/make_test_labels.py` generates the corpus in `tests/fixtures/labels/`: 24 synthetic labels spanning distilled spirits, wine, and malt classes, four layouts, six palettes, several font families, and four canvas sizes, with invented brands in varied casings. About half are compliant — two of those render the entire warning statement in capitals, which is legal because 27 CFR 16.22(a)(2) fixes only the heading's case; the rest each carry one specific defect mapped to a check the engine actually performs — warning heading in title case, heading not bold, altered statutory text, missing warning, proof ≠ 2 × ABV, and a label brand differing from the filed brand (both a typo-level variant and a different brand).

Two CSVs ride along:

- `applications.csv` (`filename,brand_name,class_type`) — the data *filed* for each label. On the brand-mismatch labels the filed brand deliberately differs from what is painted.
- `manifest.csv` (`filename,defect,expected_verdict,notes`) — ground truth for tests and evaluation. The app never reads it.

Regenerate with `uv run python tools/make_test_labels.py`. Output is deterministic for a given `--seed` on one machine; fonts differ across machines, so committed bytes are machine-specific.

To try the app with them: upload any fixture PNG on the single-label page together with its `brand_name` and `class_type` row from `applications.csv` — or upload several PNGs plus `applications.csv` itself on `/batch`. `manifest.csv` says what each label should produce on a faithful read; through the live app, the OCR corroboration can additionally hold a finding for review when the second read of a dark or decorative label is degraded.

A second, real corpus lives in `tests/fixtures/cola/`: 11 label filings from TTB's public COLA Registry, one composite PNG per application, with the brand, fanciful name, and class/type as filed. All eleven were approved by TTB, which makes them ground truth in one direction: `uv run python tools/eval_cola.py` uploads them through a running instance's `/batch` endpoint and reports per-label outcomes, where a content-rule **fail** is a candidate false positive, **needs review** is legitimate abstention (rotated and low-contrast warnings are in the set on purpose), and bold-check findings are soft ground truth because the registry disclaims the rendered typography. One documented exception: `cola_24100001000120` misspells the mandated heading as `GOVERMENT WARNING` on the label itself (approval does not guarantee textual perfection), so a wording **fail** on that row is a true positive, not a checker bug. Provenance and per-label notes: [tests/fixtures/cola/README.md](tests/fixtures/cola/README.md).

## Layout

- `src/label_assay/domain/` — the domain model. No I/O.
- `src/label_assay/rulebook/` — TTB rules **as data** (`rules/*.yaml`) plus the loader. Each rule carries a required CFR citation; `tests/test_ssot.py` fails if statutory text is hardcoded anywhere in the source.
- `src/label_assay/text/`, `.../match/` — normalization, the warning comparator, brand matching, bold detection. Pure functions.
- `src/label_assay/extract/` — the extractor port and its adapters (vision model, OCR, fixture replay).
- `src/label_assay/verify/` — the compliance engine and the legibility gate.
- `src/label_assay/web/` — FastAPI app, templates, batch runner, spend guard.

## Limitations

Rules regulated in millimetres (type size, characters per inch, contrasting background) are **not** checked: a flat image carries no physical scale, so they are unverifiable from the artifact and are reported as not evaluable rather than guessed.

**Batch throughput is bound by the host CPU.** Every label runs local OCR — a detection and recognition pass, i.e. sustained CPU work. On the previous host, a burst-credit shared-CPU tier, this measured ~2.3s per label until the credits ran out and 20–30s per label after — the reason a 300-label batch could not complete there. The current host runs two full vCPUs with no burst mechanic, so the sustained rate is the steady rate; the measured figure for this host is recorded with the deployment checks in `tools/verify_deploy.py` output rather than promised here.

**A batch upload is capped at 150 MB total**, because uploads are held in process memory on a 2 GB machine. At registry-grade image sizes (~1 MB average) that is roughly 150 labels per upload, so a 300-application drop is two or three sub-batches; an oversized upload is asked to split, and the upload page states the limit.

**Single-label latency is bound by the slower of the two reads.** The OCR pass and the vision-model call run concurrently, so a check costs the slower one, not the sum. The result page prints the measured time for every check; the 5-second target holds when the host CPU is not saturated by a concurrent batch, and degrades with it — stated because a compliance agent checking one label mid-batch will see it.

**Hosting sleeps when idle.** The deployed instance runs on a free-tier Space that suspends after long inactivity; a scheduled health check (.github/workflows/uptime.yml) probes it every six hours, which keeps it warm and emails on any degraded subsystem. If it ever lapses, the first request pays a cold start of about a minute.


Full list, with reasoning: [docs/DESIGN.md](docs/DESIGN.md).

## Approach, tools, and assumptions

See [docs/DESIGN.md](docs/DESIGN.md) and the decision records in [docs/adr/](docs/adr/).

This project was built with AI assistance (Claude). Every regulatory value was verified against the primary source (eCFR and the U.S. Code) rather than taken from the model, tests were written alongside the code they cover, and the author is accountable for every line. All but three commits carry an `Assisted-by:` trailer. Details in DESIGN.
