# Design

> Skeleton. Fills in as the build proceeds; the section headings are the plan.

## Goals

- Verify an alcohol beverage label image against its application data and against TTB requirements (27 CFR).
- Return **pass / needs review / fail**, each finding carrying the specific rule and its CFR citation.
- Under 5 seconds per label.
- Batch of 200–300 labels.
- Usable by a non-technical reviewer.
- No hard dependency on an outbound cloud ML endpoint (degrades to local checks when the endpoint is unreachable).

## Non-goals (deliberately out of scope)

- **Dewarping / glare / perspective correction.** COLA registry label images are flat print artwork, not bottle photos, so this subsystem would be dead code.
- **Type size / characters-per-inch / contrasting-background checks.** Regulated in millimeters (27 CFR 16.22(b), 5.53, 7.53); a flat image carries no DPI or physical-scale reference, so these are physically unverifiable from the artifact. Routed to `NOT_EVALUABLE` with the citation. TTB Form 5100.31 itself states TTB does not routinely review for these.
- **"Same field of vision" placement** (27 CFR 5.63(a)) — geometric (40% of a cylinder's circumference); unverifiable from a flat image.
- **COLA system integration** — out of scope per the assignment.
- **Persisting sensitive data** — images are processed in memory; only a content hash is retained.

## Architecture (summary; see `adr/`)

- **Hybrid.** A deterministic OCR spine reads characters; one vision model reads the image independently. Neither decides compliance — verdicts are computed in a pure Python core against the rulebook.
- **Rules as data.** Every TTB rule lives in `rulebook/rules/*.yaml` with a required CFR citation. The engine dispatches match strategies from a closed vocabulary; it never branches on an individual rule.
- **One port.** The extractor is swappable (hosted vision model / local OCR / in-tenant Azure / fixture replay) because the client's firewall blocked the previous vendor's cloud endpoint. Other seams are not ports — no stated second implementation, no abstraction.
- **Three-state verdict.** A label that cannot be read routes to a human, never a silent fail.

## Trade-offs and limitations

(Written up at build completion, per the assignment's request to document trade-offs.)

## AI assistance

This project is built with AI assistance (Claude). AI output is treated as a suggestion, reviewed and tested before it lands; every regulatory value is verified against the primary source (eCFR / the U.S. Code). Commits carry an `Assisted-by:` trailer. The developer is accountable for every line.
