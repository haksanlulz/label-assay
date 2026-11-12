# Synthetic 100-label scored run, 2026-11-12

The same 100 generated labels as the 2026-11-10 run, through `/batch` on the verdict logic as it stands now, scored per label against the generator's manifest. `ledger.json` has one row per label.

| | Total | Passed | Held for review | Failed |
|---|---|---|---|---|
| Compliant | 50 | 35 | 15 | 0 |
| Defective | 50 | 0 | 13 | 37 |

- False passes: 0. False fails: 0. Processing errors: 0.
- Exact match to the manifest's expected verdict: 84 of 100. The other 16 are holds: 15 compliant labels and 1 defective one the manifest expected to fail (a title-case heading). The remaining 12 defective holds are expected holds: the 8 labels with no warning at all, and 4 brand mismatches.
- 121 seconds for the batch on the local machine.

## How it was run

- Engine: commit `6358ad2`, local `uvicorn` on `127.0.0.1`, reader model `claude-haiku-4-5-20251001`.
- Corpus: `uv run python tools/make_test_labels.py --seed 20261110 --count 100 --out <dir>`. `manifest.csv` and `applications.csv` are byte-identical to the 2026-11-10 run's.
- Scoring: `uv run python tools/score_batch.py --base-url http://127.0.0.1:8790 --corpus <dir> --out <dir>`.

## Why the hold count moved

Five runs were made the same day, on the same labels.

| Code | Compliant passed / held | Defective failed / held |
|---|---|---|
| `a2160cb`, before the reader change | 27 / 23 | 33 / 17 |
| `a2160cb`, repeat | 26 / 24 | 33 / 17 |
| `6358ad2`, the reader change before it was committed | 35 / 15 | 36 / 14 |
| `6358ad2`, repeat | 35 / 15 | 37 / 13 |
| `6358ad2`, this ledger | 35 / 15 | 37 / 13 |

None of the five had a false pass or a false fail. Only the last one's ledger is kept.

The two `a2160cb` runs agree in total and disagree label by label: 15 labels changed verdict between them, all on the warning rules, all to or from a hold. The three `6358ad2` runs differ by one label.

The cause was measured. The reader used to be asked for the warning twice, a quote and a value, and only the quote is judged. In 13 of 40 calls made with the old request on eight of these labels, the reader put a one-line summary in the value slot ("Government warning present with standard pregnancy and impairment warnings") instead of the text. The summary scored under the floor against the scan, and the whole warning was held with the note "a second reading of the label did not corroborate this". The quote scored 1.0 against the scan in all 40. The value slot for the warning is now told to stay null. The quote still has to be corroborated by the scan before anything is decided.

Between the `6358ad2` runs one label moved. Its warning has one word altered; the reader wrote the standard word in one run and the printed word in the others. The first is held with the scan's differing words named, the others fail. That is the reader, not the rules, and it is the known limit on a misprint the reader corrects.

## What it does not show

The generator pins its ground truth to the engine's own matchers, so this run says the full path (upload, reader, OCR, rules, export) agrees with the corpus it was built against. It is not an accuracy figure for real label artwork. Fifteen of the fifty compliant labels were still held for a person.
