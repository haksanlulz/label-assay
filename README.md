# LabelAssay

Checks alcohol beverage labels against TTB labeling requirements (27 CFR parts 4, 5, 7, and 16). Upload a label image and the details filed on the application; get **compliant / needs review / needs correction**, with the specific rule and CFR citation behind each finding.

**Live demo:** https://label-assay.fly.dev — `/sample` runs a bundled label end to end.

## Why it works this way

The reading is done by AI. The deciding is not.

A vision model transcribes what is on the label. Compliance verdicts are then computed in plain Python against a rulebook of TTB rules held as data. Two reasons drove that split:

- **A model will confidently pass a non-compliant label.** Vision models reproduce the Government Warning from memory instead of reading it — they score near-perfectly on canonical images and badly on altered ones. A label whose warning is subtly wrong *is* an altered image. So the statutory text is compared byte-for-byte in code, where it either matches or it does not.
- **Speed.** The prior vendor took 30–40 seconds per label, so reviewers went back to checking by eye. One terse model call plus microseconds of deterministic checking stays inside the 5-second target.

An independent OCR pass reads the same image. Where the two readings disagree, the finding is held for a human rather than passed or failed — the two channels fail in different ways, so their agreement is real evidence in a way a model's self-reported confidence is not.

## What it checks

| Check | Citation |
|---|---|
| Health warning statement, verbatim | 27 CFR 16.21 |
| Warning heading in bold, remainder not | 27 CFR 16.22(a)(2) |
| Brand name matches the application | 27 CFR 5.64 |
| Alcohol content internally consistent (proof = 2 × ABV) | 27 CFR 5.65, 5.1 |

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

Open <http://127.0.0.1:8000>. Health at `/health`, the bundled sample at `/sample`, batch at `/batch`.

With Docker:

```
docker compose up             # http://localhost:8080
```

Tests:

```
uv run pytest                 # live-API tests skip cleanly without a key
```

Regenerate the sample labels: `uv run python samples/make_samples.py`.

## Layout

- `src/label_assay/domain/` — the domain model. No I/O.
- `src/label_assay/rulebook/` — TTB rules **as data** (`rules/*.yaml`) plus the loader. Each rule carries a required CFR citation; `tests/test_ssot.py` fails if statutory text is hardcoded anywhere in the source.
- `src/label_assay/text/`, `.../match/` — normalization, the warning comparator, brand matching, bold detection. Pure functions.
- `src/label_assay/extract/` — the extractor port and its adapters (vision model, OCR, fixture replay).
- `src/label_assay/verify/` — the compliance engine and the legibility gate.
- `src/label_assay/web/` — FastAPI app, templates, batch runner, spend guard.

## Limitations

Rules regulated in millimetres (type size, characters per inch, contrasting background) are **not** checked: a flat image carries no physical scale, so they are unverifiable from the artifact and are reported as not evaluable rather than guessed. Batch mode runs label-internal checks only. Full list, with reasoning: [docs/DESIGN.md](docs/DESIGN.md).

## Approach, tools, and assumptions

See [docs/DESIGN.md](docs/DESIGN.md) and the decision records in [docs/adr/](docs/adr/).

This project was built with AI assistance (Claude). Every regulatory value was verified against the primary source (eCFR and the U.S. Code) rather than taken from the model, tests were written alongside the code they cover, and the author is accountable for every line. Commits carry an `Assisted-by:` trailer. Details in DESIGN.
