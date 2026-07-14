# LabelAssay

Checks alcohol beverage labels against TTB labeling requirements (27 CFR parts 4, 5, 7, and 16). Upload a label image plus its application data; get **pass / needs review / fail** with the specific rule and CFR citation behind each finding.

**Status: in development.** This is the skeleton — the web app runs and deploys, the rulebook is loading, and the single-source-of-truth discipline is wired. Extraction, verification, and the upload UI land in later stages.

## Run it

    uv sync
    uv run uvicorn label_assay.web.app:app --reload

Open <http://127.0.0.1:8000>. Health check at `/health`.

## Or with Docker

    docker compose up

Open <http://localhost:8080>.

## Tests

    uv run pytest

## Layout

- `src/label_assay/domain/` — pure domain model (value objects, entities). No I/O.
- `src/label_assay/rulebook/` — the TTB rules **as data** (`rules/*.yaml`) plus the loader. The single source of truth for every rule and its CFR citation. `tests/test_ssot.py` enforces that no statutory text is hardcoded elsewhere.
- `src/label_assay/extract/` — the extractor port and adapters (Day-3 stage).
- `src/label_assay/verify/` — the pure compliance engine (Day-4 stage).
- `src/label_assay/web/` — the FastAPI app and templates.

## Approach, tools, and assumptions

See [docs/DESIGN.md](docs/DESIGN.md). AI assistance is disclosed there and in the commit trailers.
