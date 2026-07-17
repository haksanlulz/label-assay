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
- **Dewarping, glare, perspective correction.** Most COLA label images are flat print artwork, but real filings do include bottle photography — one of the eleven real-registry labels in `tests/fixtures/cola/` is a pair of bottle photographs with curved label text. The honest behavior there is abstention (unreadable fields go to review), not a half-built correction pipeline; if photographic input became the norm, the extractor port (ADR-0004) is where dewarping lands.
- **Alcohol content versus the application.** TTB Form 5100.31 has *no* alcohol-content field, so there is nothing on the application to compare against. Alcohol content is therefore checked for internal consistency (proof = 2 × ABV, 27 CFR 5.1) instead. The CFR's ±0.3 percentage-point tolerance is a **label-versus-laboratory** allowance — applying it to a document comparison would forgive a real data-entry discrepancy, which is why it is not used that way here.
- **Brand matching for batch labels with no filed application row.** A batch upload may omit `applications.csv`, or a label may have no matching row; there is nothing filed to compare against, so the brand match is reported not evaluable rather than forced. With the CSV (`filename, brand_name, class_type`), each label in a batch is checked against its own filed brand and class — the same check set as the single-label path.
- **COLA system integration**, and **persisting anything sensitive** — images are processed in memory.

## How it works

1. **Read.** A vision model (Claude Haiku) transcribes the label into a fixed schema. It is blind by construction: it receives only the image, never the application data and never the OCR output, so it cannot "find" an expected answer. The schema forces it to quote the printed text before committing to a value, and to mark a field absent rather than fill a required slot.
2. **Read again, independently.** A local OCR pass (RapidOCR) reads the same image offline, with per-line confidence.
3. **Decide.** A pure function verifies the extraction against the rulebook and the application. No model is consulted. Each rule's match strategy is dispatched from the rulebook; the engine never branches on an individual rule.
4. **Gate.** Where OCR cannot corroborate the model's quoted text, that finding is held for review — never passed or failed. Absence of OCR evidence is never treated as evidence of absence: an unreadable image singles out no field. For the mandated warning the bar is strict: a pass stands only when the OCR read contains the statutory text itself, because a vision model can recite that paragraph over a label that prints something else.
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

- **Cost is estimated, not metered.** The daily spend guard bounds the public instance using a conservative per-label estimate rather than actual token usage. The provider-side workspace spend cap is the real ceiling. A failed reader call does not refund its budget reservation and a batch has no failure breaker, so a provider outage can exhaust the app-side daily budget with almost nothing actually spent at the provider; the next day's reset clears it, and metering real usage from the API response would remove the estimate entirely.
- **Reader failures are not differentiated.** Every reader failure renders the same try-again message; a revoked key or a bad model id looks transient to the user, and distinguishing provider error classes (auth, not-found, rate-limit) behind the extractor port is a production step.
- **Prompt caching: evaluated and rejected.** The extractor's only stable prefix is its ~400-token tool schema, an order of magnitude under Haiku 4.5's 4,096-token minimum cacheable prefix, so caching cannot engage. The dominant cost is the per-label image, which is unique per request. Revisit if the prompt ever grows past the minimum (e.g. few-shot examples).
- **Batch throughput is host-bound, and the deployed instance is on a shared-CPU tier.** This is the sharpest limitation and it is worth being precise about, because it is not a code defect. Every label runs local OCR, which is sustained CPU. A shared-CPU machine grants burst credits and then throttles to a fraction of a core. Measured, on identical work back to back: **~2.3s per label on burst, then 20–30s per label once spent — roughly a tenfold cliff after roughly 20 labels.** A 25-label batch completes in ~164s. A 300-label batch does not complete on this tier: throttled, the machine eventually stops keeping up and the run is lost with it. Dedicated CPU holds the burst rate; the pipeline is not the bottleneck, the machine is. On dedicated CPU the same code holds the burst rate, which puts 300 labels — run as sub-batches under the upload cap below — in the ten-minute range. The tempting shortcut — dropping OCR from the batch path to make it fast — was rejected: it would delete the confidence cross-check and the bold check at scale, making bulk mode quietly less rigorous than single-label. A compliance tool should not have a weaker mode.
- **A batch upload is capped at 150 MB total.** Uploads are held in process memory on a 2 GB machine, so the cap is a memory bound. At registry-grade image sizes (~1 MB average across `tests/fixtures/cola/`) roughly 150 labels fit in one upload, which means a 300-application drop is two or three sub-batches; an oversized upload gets a 413 asking to split, and the upload page states the limit. Raising the cap honestly means streaming uploads to disk or a bigger machine, not a larger constant.
- **Single-label latency is bound by the same shared-CPU budget.** Timed against the deployed instance while the two reads still ran sequentially, a single check took 5.6–8.5 s — over the 5-second target. The reads share no inputs, so they now run concurrently and a check costs max(vision call, OCR) rather than the sum; the result page prints the measured time of every check. That is expected to sit inside the target while burst credits last, but it has not been re-measured on the deployed tier (`tools/verify_deploy.py` times one live check as part of every deploy verification, which is where this number gets re-taken), and once credits are spent (a large batch drains them) OCR alone is 20–30 s and no concurrency rescues the target. On this tier the 5-second promise holds only while the machine does.
- **Batch state is in-memory and single-instance.** The job store lives in the process, so the app must run as exactly one instance. The first host provisioned two machines by default, which silently 404s a batch when the poll lands on the machine that did not create it — found the hard way, and enforced in CI thereafter; the current host runs a single container by construction. A restart loses in-flight jobs. A production deployment needs a shared job store (Redis/Postgres) and horizontal scale; one instance is a single point of failure for a queue of 300 applications. A scheduled CI job (.github/workflows/uptime.yml) probes /health every six hours and fails loudly on a degraded subsystem; a platform-native liveness probe that restarts a hung process is the production step.
- **Hosting is a cost decision, and the artifact is portable.** The deliverable is a Docker image; when the first host's trial lapsed it moved to a Hugging Face Space with zero application-code changes — the same portability the extractor-port section promises for the model endpoint, exercised for the host.
- **Bold detection abstains on small print, on heading-only lines, and on inconclusive measurements.** Real warnings are 1–2 mm; below roughly a 14px cap height the check reports review rather than committing. The measurement assumes the heading and body share a line and type size, so when OCR returns the heading as its own line — a common narrow-label layout — the check abstains to review instead of measuring the heading against a sliver of itself. A not-bold verdict additionally requires the stroke-width ratio to sit conclusively below the measurement noise around 1.0 and the heading and body cap heights to actually match: at registry print resolutions (strokes a few pixels wide) a bold-vs-regular pair can measure within a few percent of 1.0, and failing a real label on that margin would be a false verdict, so those cases go to a person.
- **Warning corroboration is character-exact.** A pass on the mandated warning requires the independent OCR read to contain the statutory text exactly (ignoring case, spacing, and punctuation). A vision model reciting the paragraph over an altered or truncated label is therefore held for review — and so is a genuine warning whose OCR read misses even a character, as happens on one dark-palette label in the generated corpus. Abstention is the designed degradation; on noisy scans the review rate rises accordingly, and a claimed-perfect warning on an image OCR cannot read at all goes to review too — that combination is the recitation threat in its purest form.
- **Brand normalization uses a curated US legal-suffix list**, not a general company-name library — an international suffix list mangled real brand words.
- **Multi-image products.** A COLA is front, back, and neck images, and the warning may legally appear on a back label. The domain reserves a surface vocabulary (`SurfaceType`) but does not yet model a multi-image product; the current flow verifies one image at a time, and verifying text-presence rules across the union of a product's images is the next structural step.
- **Per-class citations.** The brand and alcohol-content rules cite the distilled-spirits sections (Part 5). Wine (Part 4) and malt (Part 7) equivalents need their own per-class rules before those classes are properly supported.
- RapidOCR internally downscales images to a 2000px long side before detection (its config.yaml max_side_len), so on very tall multi-panel composites the second read loses most lines and findings fall to review; pre-splitting tall composites (>2:1 aspect) into panels before OCR is the known fix.
- The container runs as root; the app needs no privileges (port 8080, no writes outside /tmp), so a production image adds an unprivileged USER after the dependency sync — left undone here rather than risk an untested permission change on the deployed image during review.
- Two OpenCV distributions land in the environment — the project declares opencv-python-headless while RapidOCR hard-depends on full opencv-python, and both install into the same cv2/ path with the winner decided by install order (locally the full build wins, which is why the X11/GL apt libraries stay in the image); collapsing to one build needs a uv dependency override.

## Evaluation

Verified against a generated fixture corpus with known ground truth (`tests/fixtures/labels/`, built by `tools/make_test_labels.py`): 24 synthetic labels across spirits, wine, and malt classes, four layouts, six palettes, and four canvas sizes — about half compliant (two of those render the entire warning statement in capitals, which is legal since 27 CFR 16.22(a)(2) fixes only the heading's case, and pins the comparator's heading/body split at pixel level), the rest each carrying one specific defect mapped to a check the engine actually performs. `manifest.csv` records each label's defect and expected verdict; `applications.csv` carries the filed application data, deliberately wrong on the brand-mismatch labels. The corpus is regenerable from a seed, and its ground truth is pinned to the code from two directions: the generator asserts the text, brand, and alcohol-content defects against the engine's own matchers at build time, and the test suite verifies every manifest verdict against the engine, pushing the regular-weight headings through real OCR and the stroke-width detector on their rendered pixels. Mutation tests additionally alter the mandated warning (title-casing the heading, dropping a word) and the application data to exercise each verdict. 163 tests; the deterministic core is property-tested for idempotence, and the normalizer is proven a no-op on the verbatim 27 CFR 16.21 reference — which is what makes the word-for-word comparison trustworthy.

Real labels: `tests/fixtures/cola/` holds 11 approved filings from TTB's public COLA registry with the application-side data as filed, and `uv run python tools/eval_cola.py` drives them through a running instance's batch endpoint end-to-end. Approved labels bound the false-positive side of the ledger: a content-rule fail on one is a candidate false positive, a needs-review is legitimate abstention. Candidate, not proven — approval does not guarantee textual perfection: `cola_24100001000120` prints its heading as "GOVERMENT WARNING" on the label itself, a real defect that approval missed, so a wording fail on that row is a true positive (the corpus README documents it). **Not yet done:** a full precision/recall number. Approval makes nearly every COLA here a true pass, and one organic misspelling is not a reject class, so measuring recall would need synthesized violations on real artwork — the same mutation approach already used on the generated corpus.

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
- All but three commits carry an `Assisted-by:` trailer, following the kernel/Fedora convention.

The tool did the legwork. The author verified it, and can explain every decision in this document.
