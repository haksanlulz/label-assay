# Design

Approach, tools, and assumptions. Decision records for the contested forks are in [adr/](adr/).

## Goals

- Verify an alcohol beverage label image against the details filed on its application and against TTB requirements (27 CFR).
- Return compliant / needs review / needs correction, each finding carrying the rule and its CFR citation.
- Under 5 seconds per label — a stated kill criterion, since the prior vendor's 30–40s meant reviewers abandoned it.
- Handle a batch.
- Usable by a non-technical reviewer.
- No hard dependency on an outbound cloud ML endpoint.

## Non-goals (deliberately out of scope)

Each of these is a scoping decision, not an omission.

- **Type size, characters per inch, contrasting background.** Regulated in millimetres (27 CFR 16.22(b), 5.53, 7.53). A flat image carries no DPI or physical-scale reference, so these are *unverifiable from the artifact*. They are reported as not evaluable with the citation, rather than guessed. TTB Form 5100.31 itself states TTB does not routinely review for them.
- **"Same field of vision" placement** (27 CFR 5.63(a)) — defined geometrically (40% of a cylinder's circumference); a flat image cannot express it.
- **Dewarping, glare, perspective correction.** COLA label images are flat print artwork, not bottle photographs, so this subsystem would be dead code. If photographs became an input, the extractor port (ADR-0004) is where that lands.
- **Alcohol content versus the application.** TTB Form 5100.31 has *no* alcohol-content field, so there is nothing on the application to compare against. Alcohol content is therefore checked for internal consistency (proof = 2 × ABV, 27 CFR 5.1) instead. The CFR's ±0.3 percentage-point tolerance is a **label-versus-laboratory** allowance — applying it to a document comparison would forgive a real data-entry discrepancy, which is why it is not used that way here.
- **Brand matching in batch mode.** A batch of loose images carries no per-label application, so the brand match is reported not evaluable there rather than forced. It is a paired, single-label operation.
- **COLA system integration**, and **persisting anything sensitive** — images are processed in memory.

## How it works

1. **Read.** A vision model (Claude Haiku) transcribes the label into a fixed schema. It is blind by construction: it receives only the image, never the application data and never the OCR output, so it cannot "find" an expected answer. The schema forces it to quote the printed text before committing to a value, and to mark a field absent rather than fill a required slot.
2. **Read again, independently.** A local OCR pass (RapidOCR) reads the same image offline, with per-line confidence.
3. **Decide.** A pure function verifies the extraction against the rulebook and the application. No model is consulted. Each rule's match strategy is dispatched from the rulebook; the engine never branches on an individual rule.
4. **Gate.** Where OCR cannot corroborate the model's quoted text, that finding is held for review — never passed or failed. Absence of OCR evidence is never treated as evidence of absence: an unreadable image singles out no field.
5. **Report.** Worst finding wins: any failure fails; else any review needs review; else compliant.

### Rules as data

Every TTB rule lives in `rulebook/rules/*.yaml` with a **required** `citation` field — an uncited rule fails to load, so every finding carries its citation by construction. Code knows *strategies* (`verbatim`, `brand_match`, `abv_consistency`, `warning_bold`), never individual rules. `tests/test_ssot.py` greps the source and fails if statutory text is hardcoded anywhere outside the rulebook.

The rulebook is YAML, not a database: for a regulation, git history *is* the audit trail, and a row is not diffable. When TTB re-promulgated standards of fill in January 2025, that shape of change is a YAML edit and a test row, not a code change.

Resisted: a condition DSL inside YAML. That is an interpreter to defend. Rules use closed-vocabulary predicate fields instead.

### One port, and the ones not built

The extractor is a `Protocol` with adapters: the hosted vision model, a fixture replay for deterministic tests, and room for a local or in-tenant endpoint. This is the *one* abstraction a stated requirement justifies — the client's firewall blocked the prior vendor's cloud ML endpoint (ADR-0004).

There is deliberately no repository port, storage port, or notifier port. No stated second implementation, no port.

### Three states, not two

A compliance tool must never fail a label it merely could not read. The third state is a domain decision, not a UI one: legibility ("can we read it?") and compliance ("does what we read satisfy the rule?") are orthogonal axes, and a failure is only ever issued on positively-read evidence.

## Trade-offs and limitations

- **Cost is estimated, not metered.** The daily spend guard bounds the demo using a conservative per-label estimate rather than actual token usage. The provider-side workspace spend cap is the real ceiling.
- **Batch state is in-memory and single-instance.** Fine for one always-on machine; a production deployment needs a shared job store. Batch is capped at 50 files to bound cost and abuse.
- **Bold detection abstains on small print.** Real warnings are 1–2 mm; below roughly a 14px cap height the check reports review rather than committing. It also assumes the heading and body share a line and type size.
- **Brand normalization uses a curated US legal-suffix list**, not a general company-name library — an international suffix list mangled real brand words.
- **Multi-image products.** A COLA is front, back, and neck images, and the warning may legally appear on a back label. The domain models a product as a set of surfaces, but the current flow verifies one image at a time; verifying text-presence rules across the union of a product's images is the next structural step.
- **Per-class citations.** The brand and alcohol-content rules cite the distilled-spirits sections (Part 5). Wine (Part 4) and malt (Part 7) equivalents need their own per-class rules before those classes are properly supported.

## Evaluation

Verified against synthetic labels with known ground truth, plus mutation tests that alter the mandated warning (title-casing the heading, dropping a word) and the application data to exercise each verdict. 67 tests; the deterministic core is property-tested for idempotence, and the normalizer is proven a no-op on the verbatim 27 CFR 16.21 reference — which is what makes the byte-for-byte comparison trustworthy.

**Not yet done:** a measured precision/recall against a corpus of real approved labels from TTB's public COLA registry. That is the highest-value next step, and the registry supplies both the images and the application-side ground truth for free. Every approved COLA is a true positive by construction, so the reject class would need synthesized violations — the same mutation approach already used here.

## Governance

TTB is a Treasury bureau, and Treasury governs bureau AI under its M-25-21 AI Strategy and Compliance Plan, a departmental Chief AI Officer, and a public AI use-case inventory.

This tool is **advisory** — a specialist is the principal basis for the decision — and label approval is not among the presumed high-impact categories in OMB **M-25-21** §6. It is therefore defensibly *not* high-impact. The memo requires practices proportionate to risk; the prototype adopts them voluntarily where it can:

| M-25-21 minimum practice (§4(b)) | Where it shows up here |
|---|---|
| Pre-deployment testing | Mutation-based test set exercising each verdict — the query-and-observe method the memo names |
| AI impact assessment | This document's non-goals, trade-offs, and limitations |
| Ongoing monitoring | Re-runnable test suite; graceful degradation. *Continuous monitoring is stubbed* |
| Adequate human training | Operator-legible UI; the review state directs attention. *A formal programme is a gap* |
| Human oversight and intervention | Advisory three-state, never auto-deny; fail-safe degradation |
| Remedies or appeals | Advisory, so TTB's existing human review path stays intact; per-finding citation for traceability |
| End-user feedback | The review state and per-finding reasons are the reviewer's surface. *A public feedback channel is a gap* |

Productionizing under M-25-21 would additionally require a signed impact assessment with an independent reviewer, post-deployment monitoring, a formal training programme, an integrated appeals path, and a CAIO determination plus an entry in Treasury's AI use-case inventory.

Framed against the NIST AI Risk Management Framework 1.0 (govern / map / measure / manage). Not the Generative AI Profile — this is a discriminative pipeline, and the vision model transcribes rather than generates the verdict.

## Accessibility

Built to WCAG 2.1 AA to be Section 508-ready; see [../ACCESSIBILITY.md](../ACCESSIBILITY.md). The single-label flow is server-rendered and works with JavaScript disabled. This is also why a dashboard framework was not used: Streamlit and Gradio have no 508 conformance story.

## AI assistance

Built with AI assistance (Claude). The working rule was command and verification, not delegation:

- Every regulatory value — the verbatim warning, tolerances, class minimums, standards of fill — was verified against the primary source (eCFR XML, govinfo, 27 U.S.C. § 215), not taken from the model. Model-supplied regulatory claims were wrong at least once in ways that would have shipped a fail-open bug (see the alcohol-content tolerance note under Non-goals).
- Tests were written alongside the code they cover and caught real defects before commit, including an over-aggressive suffix strip and a non-idempotent normalization ordering.
- Commits carry an `Assisted-by:` trailer, following the kernel/Fedora convention.

The tool did the legwork. The author verified it, and can explain every decision in this document.
