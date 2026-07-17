# Real-label evaluation corpus (TTB Public COLA Registry)

Eleven real, TTB-approved label applications pulled from the [Public COLA Registry](https://ttbonline.gov/colasonline/publicSearchColasBasic.do) on 2026-07-17. Each `cola_<TTBID>.png` is the label set filed on that application; multi-panel filings (front + back) are stacked vertically into one width-matched image, because the app's contract is one image per application. `applications.csv` carries the data as filed, taken from the registry record: `filename,brand_name,class_type` — the same schema the batch parser expects.

COLA records are public records of a federal agency. The label artwork itself may carry applicants' trademarks; these files are test fixtures for compliance-checking, not assets for reuse.

## What "approved" means for expectations

Every label here was approved by TTB, so the content rules should not fail on them, with one documented exception below: a warning-wording, alcohol-content, or brand-vs-application **fail** on this corpus is a candidate false positive (or an OCR failure, which must surface as *needs review*, never as a fail), to be checked against the notes before it is filed as a checker bug. Approval does not guarantee textual perfection: `cola_24100001000120` misspells the mandatory heading on the label itself, and a wording **fail** on that row is a true positive. Typography is softer ground truth: TTB's registry disclaims the rendered type ("may appear differently, with respect to type size, characters per inch and contrasting background, than actual labels"), so a *needs review* from the bold check against a decorative display face is acceptable behavior and a *fail* is suspect.

Stress cases and known deviations this corpus includes (verified by inspecting the composites):

- `cola_25178001000103` (Nascent Spirits bourbon): the entire warning is printed rotated 90° along the right edge, white on black. A legibility-gate test: OCR that cannot read it must hold the finding for review, never fail it.
- `cola_24066001000900` (The Greek Theatre): the warning is likewise rotated 90° in a narrow side panel, and the fanciful names ("THIRST TRAP", "WATERMELON SUGAR HIGH") are far more prominent than the brand — brand-matching noise as it actually occurs on craft labels.
- `cola_25178001000103` and `cola_24093001000375`: the warning **body** is set in all capitals. 27 CFR 16.22(a)(2) mandates capitals and bold only for the heading words; an all-caps body is legal and common on real labels. These catch case-handling false positives.
- `cola_24064001000356` (Mortalis): a keg collar, not a bottle label — calendar date ring around the rim, checkbox net contents in U.S. gallons, and the alcohol content as a fill-in blank ("8 __% Alc./Vol."). Layout noise as filings actually look.
- `cola_24093001000375`: the filed brand name is `7` while the label art reads "VODKA 7" — the brand-judgment case (27 CFR 5.64) on a one-character brand.
- `cola_24100001000120` (Alsina & Sardà): the printed heading reads `GOVERMENT WARNING:` — missing the first N of GOVERNMENT (the one before MENT). A real on-label defect that TTB approval did not catch, plainly visible in the composite at zoom. A warning-wording **fail** (altered) on this row is a true positive: the one row in the corpus where a content-rule fail is correct behavior, not a checker bug.

## Provenance

| TTB ID | Brand (as filed) | Class/type (as filed) | Panels |
|---|---|---|---|
| 24062001000014 | PADRON | OTHER GRAPE BRANDY (PISCO GRAPPA) FB | front + back |
| 24064001000356 | MORTALIS BREWING COMPANY | MALT BEVERAGES SPECIALITIES - FLAVORED | single (keg collar) |
| 24065001000802 | TWO BROADS CIDERWORKS | APPLE TABLE WINE/CIDER | single |
| 24066001000900 | THE GREEK THEATRE | RUM SPECIALTIES | single (can) |
| 24071001001099 | EARTHBOUND BEER | BEER | single (can) |
| 24093001000375 | 7 | VODKA 80-89 PROOF | front + back |
| 24100001000120 | ALSINA & SARDA | SPARKLING WINE/CHAMPAGNE | 2 panels |
| 24100001000210 | ANDECHS | ALE | front + back |
| 24106001000404 | VIN A PORTER | CARBONATED WINE | single |
| 25150001000637 | LA CUADRILLA | TABLE RED WINE | 2 panels |
| 25178001000103 | NASCENT SPIRITS | STRAIGHT BOURBON WHISKY | single |

Source record for any row: `https://ttbonline.gov/colasonline/viewColaDetails.do?action=publicDisplaySearchBasic&ttbid=<TTB ID>`. Images were fetched at human-browsing pace through the registry's own public pages.

Run the evaluation against a running instance: `uv run python tools/eval_cola.py --base-url http://127.0.0.1:8000` (see the script's docstring). `tests/test_cola_corpus.py` checks corpus integrity offline.
