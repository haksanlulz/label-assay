# Synthetic 100-label scored run, 2026-11-10

One batch of 100 generated labels through `/batch`, scored per label against the generator's manifest. `ledger.json` has one row per label.

| | Total | Passed | Held for review | Failed |
|---|---|---|---|---|
| Compliant | 50 | 26 | 24 | 0 |
| Defective | 50 | 0 | 14 | 36 |

- False passes: 0. False fails: 0. Processing errors: 0.
- Exact match to the manifest's expected verdict: 74 of 100. The other 26 are holds: 24 compliant labels and 2 defective ones (one not-bold heading, one title-case heading) the manifest expected to fail.
- 130 seconds for the batch on the local machine.

## How it was run

- Engine: commit `c515dc3`, local `uvicorn` on `127.0.0.1`, reader model `claude-haiku-4-5-20251001`.
- Corpus: `uv run python tools/make_test_labels.py --seed 20261110 --count 100 --out <dir>`. `manifest.csv` and `applications.csv` here are that run's output. The images are not committed; the generator's own notes say fonts make the bytes machine-specific.
- Scoring: `uv run python tools/score_batch.py --base-url http://127.0.0.1:8790 --corpus <dir> --out <dir>`.

## What it does not show

The generator pins its ground truth to the engine's own matchers, so this run says the full path (upload, reader, OCR, rules, export) agrees with the corpus it was built against. It is not an accuracy figure for real label artwork. Half the compliant labels were held for a person, which is how the false-fail count stays at zero.

## Earlier run

`docs/DESIGN.md` used to cite a 100-label run from 2026-07-17 as 49 defective and 51 compliant with "19 passed, 31 abstained". Those two numbers account for 50 of the 51, and that run's per-label results were not kept. This run replaces it.
