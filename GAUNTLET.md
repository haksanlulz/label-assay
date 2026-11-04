# GAUNTLET — label-assay

Constraint state for this project (SSoT). Created by `/gauntlet convert`
2026-07-29. Operator owns §1 and §5; Claude maintains §2–§4 and §6.

**Read this first if you came from the workspace gauntlet expecting a gap.** The
workspace `/gauntlet convert` ranked this project #2 on the premise that it was
"deployed with nothing hitting the deployed URL." That premise was **wrong**.
This repo already has the strongest deployed-channel instrumentation in the
portfolio — `tools/verify_deploy.py`, a 6-hourly `uptime.yml` running a real
end-to-end check, and a `hf-deploy.yml` deploy workflow. What was missing was
this file naming them, and the handful of things they still do not cover.

## §1 Oracle — done-definition

- **It is**: a TTB label-compliance checker (27 CFR parts 5 and 16). Upload a
  label image plus the details filed on the application; get compliant / needs
  review / needs correction, each finding carrying its CFR citation.
  Live at `https://haksanlulz-label-assay.hf.space`.
- **DONE means**: the reading is done by AI and **the deciding is not** — every
  verdict is computed in plain Python against a rulebook held as data, and the
  deployed instance serves the same rulebook as the tree that claims it.
- **Non-goals** (all stated in the README, none of them defects): wine and malt
  equivalents in parts 4 and 7; net contents is extracted but has no rule; class
  and type selects which rules apply but is never compared to the application;
  bottler name, address and country of origin are neither read nor checked.

⚠️ §1 is transcribed from the README, not elicited. Unratified — unlike the
workspace gauntlet, whose §1 the operator ratified by interview on 2026-07-29.

## §2 Channel map

**A test suite is one channel; it is never the artifact's channel.** This project
is where that lesson was learned the hard way: a green build, HTTP 200 and a
passing suite, with **4/4 real bugs reachable only against the deployed
instance**.

| Artifact | Real channel | Pass condition | Rung? |
|---|---|---|---|
| **deployed app** | a stranger loads the URL and checks a label | `/health` ready · deployed rulebook == this tree's · a real fixture check returns correct findings | ✅ **`tools/verify_deploy.py`** — verified live 2026-07-29: instance `r-haksanlulz-...-gmjck`, rulebook `d0550ba69224`, 3.3s round trip, exit 0 |
| deployed app, over time | it is still up tomorrow | `/health` answers, one real end-to-end check succeeds | ✅ `.github/workflows/uptime.yml`, `cron: 17 */6 * * *` — a real label image and CSV, not a ping |
| the verdict | the finding a reviewer acts on | judged **per finding**, not by overall verdict | ✅ `check_problems()` — deliberately does not accept a passing verdict as proof the 16.21 check ran |
| prod hygiene | the demo route must not exist in prod | `GET /sample` → 404 | ✅ asserted by `verify_deploy.py` |
| the second channel | OCR degrades or dies | health degrades; findings hold rather than pass; no internals leak | ✅ 3 tests in `test_smoke.py` / `test_service.py` |
| the rulebook | a rule change reaches prod | served version matches the tree | ✅ version-pinned in `verify_deploy.py` |
| **speed** | a reviewer waits, or goes back to checking by eye | the README's **5-second target** | 🔴 **REPORTED, NOT ASSERTED** — see gaps |
| the repo | a stranger clones and runs `pytest` | 270 tests green | ✅ |

## §3 Invariants — scans

| Invariant | Scan | Status |
|---|---|---|
| The deciding layer never calls a model | ADR-0002 + `verify/engine.py` is pure by construction | 🟡 documented and true, but no AST scan enforces it (a sibling project has one — portable) |
| Rules live in data | `rulebook/rules/*.yaml` + ADR-0003 | ✅ structural |
| A model reciting the warning cannot pass an altered label | independent OCR must contain the statute | ✅ the legibility gate |
| Deployed rulebook == tree rulebook | `verify_deploy.py` | ✅ |

## §4 Ladder

| Class | Rungs |
|---|---|
| docs-only | none |
| code-touch | `pytest` (270) |
| rulebook edit | + `pytest` + **`verify_deploy.py` after deploy** (the version pin is the point) |
| deploy | + `verify_deploy.py` against the **live URL**, never localhost |

## §5 Acceptance specs

*(Operator-owned. None authored. The four MUST-NEVER-shaped candidates worth
interviewing on: a non-compliant label passing; a compliant label failing; the
deployed rulebook silently diverging from the tree; an answer arriving too slowly
to be used.)*

## §6 Escape log

### 2026-11-03 — Ambiguous application pairing and incomplete-check disclosure (gpt-6-astra)

CSV rows sharing a normalized filename silently overwrote one another, and duplicate headers or replacement-decoded UTF-8 could change the filed brand before comparison. The importer now rejects duplicate filename rows, duplicate normalized columns, invalid UTF-8, and extra unquoted cells. Duplicate uploaded names are rejected before work starts; rejected batches clean up their spooled files. A single-label PASS containing NOT_EVALUABLE findings now renders "Checks Incomplete" in the review color and counts the unchecked findings. Rulebook data and engine verdicts are unchanged.

Nine new regressions failed before the fixes (the rendering test's initial fixture-construction error was corrected before its red assertion was measured). Final: **279 passed, including all three live vision tests**, and `ruff check .` passed. Through the actual localhost server, `verify_deploy.py --base-url http://127.0.0.1:8790` passed in 4.0 seconds, the browser displayed the incomplete-check result from a real vision/OCR read, three malformed batch requests returned the expected 400/415, and a valid batch completed with a matched application and CSV export. These are local-channel checks; production was not deployed or claimed to contain the changes.

## Known gaps, ranked by blast radius

1. **The 5-second target is measured and never enforced.** `verify_deploy.py`
   extracts the instance's own elapsed time and *prints* it — no threshold, no
   comparison. The README states the target and openly says whether the instance
   clears it "depends on its CPU budget," so this is the project's own stated
   uncertainty with no rung on it. It matters because slowness is the failure
   that already happened once: a 30 to 40 second tool went unused and reviewers went
   back to checking by eye. Measured 2026-07-29 at **3.2s on the instance** —
   inside target today, which is exactly when a threshold is cheap to add.
2. **§1 and §5 are unratified.** §1 is transcribed from the README; §5 is empty.
   The operator ratified the workspace's §1 by interview the same day, so the
   pattern exists — this one just has not been run.
3. **Purity is documented, not scanned.** ADR-0002 says the deciding layer never
   calls a model and that is true today. A sibling project enforces the same property
   with an AST scan that fails the suite on a network or model import; it would
   port here in minutes.
4. **Cold start is unprobed.** HF Spaces sleep. `uptime.yml` every 6 hours keeps
   it warm and would mask a slow first request, so nobody knows what a genuinely
   cold visitor experiences. Related to gap 1 and probably the worse half of it.
